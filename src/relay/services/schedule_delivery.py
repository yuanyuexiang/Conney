from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation, localcontext
from typing import Literal, cast

from sqlalchemy import Connection, RowMapping, text

from relay.services.delivery import (
    DraftEntry,
    DraftMaterial,
    DraftSnapshot,
    MaterialMapping,
    PlanQuantity,
    PlanQuantityKind,
    find_material_mapping,
)

ScheduleCode = Literal["kroschu_4905", "vitesco_delfor", "jlr_delfor", "jlr_deljit"]


@dataclass(frozen=True)
class ScheduleOutput:
    title: str
    customer: Literal["kroschu", "vitesco", "jlr"]
    number: str
    timestamp: str
    item: str
    order: str
    line: str
    site: str
    supplier: str
    unit: str
    sheets: tuple[str, ...]

    def version(self, code: str) -> str:
        return f"{code}-1"


SCHEDULE_OUTPUTS: dict[ScheduleCode, ScheduleOutput] = {
    "kroschu_4905": ScheduleOutput(
        "科伯舒特 4905",
        "kroschu",
        "NewTransmissionNo",
        "TransferDate",
        "CustomerItemNo",
        "OrderNo",
        "OrderLineNo",
        "CustomerPlant",
        "SupplierItemNo",
        "ST",
        ("按交货日期汇总", "交货预测周表", "ETD月小计", "需求与来源", "待核对"),
    ),
    "vitesco_delfor": ScheduleOutput(
        "舍弗勒 Vitesco",
        "vitesco",
        "DelforNo",
        "DelforCreateDate",
        "BuyerItemNo",
        "BlanketOrderNo",
        "LineItemNo",
        "ShipToNo",
        "SellerItemNo",
        "C62",
        ("交货预测周表", "库存测算", "需求与来源", "待核对"),
    ),
    "jlr_delfor": ScheduleOutput(
        "捷豹路虎 DELFOR",
        "jlr",
        "DelforNo",
        "DelforCreateDate",
        "BuyerItemNo",
        "poNo",
        "POLineNo",
        "ShipToNo",
        "SupplierItemNumber",
        "EA",
        ("周需求", "需求与来源", "待核对"),
    ),
    "jlr_deljit": ScheduleOutput(
        "捷豹路虎 DELJIT",
        "jlr",
        "DeljitNo",
        "DeljitCreateDate",
        "BuyerItemNo",
        "poNo",
        "POLineNo",
        "ShipToNo",
        "SupplierItemNumber",
        "EA",
        ("周需求", "需求与来源", "待核对"),
    ),
}

Source = dict[str, dict[str, str | None]]
KROSCHU_TRANSIT_DAYS = 98
_MAPPING_COLUMNS = ", ".join(MaterialMapping.model_fields)


def _value(fields: Source, key: str) -> str | None:
    return fields.get(key, {}).get("value") or None


def _cell(fields: Source, key: str) -> str | None:
    return fields.get(key, {}).get("cell")


def week_start(value: date) -> date:
    return value - timedelta(days=value.weekday())


def _date(raw: str | None, anchor: date) -> date | None:
    if not raw:
        return None
    try:
        if len(raw) == 6 and raw.isascii() and raw.isdecimal():
            short_year = int(raw[:2])
            year = anchor.year + (short_year - anchor.year + 50) % 100 - 50
            return date(year, int(raw[2:4]), int(raw[4:6]))
        if len(raw) == 8 and raw.isascii() and raw.isdecimal():
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:]))
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _within_horizon(value: date | None, anchor: date) -> date | None:
    if value is not None and 1900 <= value.year <= 9998 and abs(value.year - anchor.year) <= 10:
        return value
    return None


def _decimal(raw: str | None) -> Decimal | None:
    if raw is None:
        return None
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return None
    return number if number.is_finite() and number >= 0 and number.adjusted() <= 60 else None


def _result(
    entry: DraftEntry,
    kind: PlanQuantityKind,
    basis: str,
    *,
    amount: Decimal | None = None,
    start: date | None = None,
    end: date | None = None,
    issue: str | None = None,
) -> None:
    entry.plan = PlanQuantity(
        kind=kind,
        amount=amount,
        start=start,
        end=end,
        basis=basis,
        issue=issue,
        raw_start=entry.plan.raw_start if entry.plan else None,
        raw_end=entry.plan.raw_end if entry.plan else None,
    )


def _review(entry: DraftEntry, issue: str) -> None:
    _result(entry, "review", "保留原值", issue=issue)


def _forecast(entry: DraftEntry, basis: str) -> None:
    first, last = entry.earliest_date, entry.latest_date
    if first is None or last is None or last < first:
        _review(entry, "缺少有效的交货区间")
    elif first.weekday() == 0 and (last - first).days == 6:
        _result(entry, "week", basis, amount=entry.quantity, start=first, end=last)
    else:
        _result(
            entry,
            "interval",
            basis,
            amount=entry.quantity,
            start=first,
            end=last,
            issue="交货区间未按整周分配",
        )


def _apply_schedule(
    code: ScheduleCode, entries: list[DraftEntry], block: Source, header: Source, anchor: date
) -> None:
    contract = SCHEDULE_OUTPUTS[code]
    cumulative: list[DraftEntry] = []
    details: list[DraftEntry] = []
    for entry in entries:
        raw = entry.plan.raw_start if entry.plan else None
        if code == "kroschu_4905" and raw == "000000" and entry.quantity == 0:
            _result(entry, "placeholder", "零量占位")
            continue
        if entry.unit != contract.unit:
            _review(entry, f"数量单位待确认（原值 {entry.unit or '空'}）")
        elif entry.quantity < 0:
            _review(entry, "负数需求的业务口径待确认")
        elif code == "kroschu_4905":
            if raw in {"000000", "333333"}:
                _result(
                    entry,
                    "special",
                    "特殊日期单列",
                    amount=entry.quantity,
                    issue=f"日期代码 {raw} 的交货动作待确认",
                )
            elif entry.requested_date is None:
                _review(entry, "缺少有效的交货日期")
            else:
                _result(
                    entry,
                    "day",
                    "按实际交货日期",
                    amount=entry.quantity,
                    start=entry.requested_date,
                )
        elif code == "vitesco_delfor" and entry.code == "113":
            _forecast(entry, "按原始交货周区间")
        elif code == "jlr_delfor" and entry.code == "3":
            if (
                entry.earliest_date
                and entry.latest_date
                and entry.latest_date >= entry.earliest_date
            ):
                cumulative.append(entry)
            else:
                _review(entry, "累计数量缺少有效交货区间")
        elif code in {"jlr_delfor", "jlr_deljit"} and entry.code == "1":
            if entry.requested_date is None:
                _review(entry, "明细数量缺少有效交货日期")
            else:
                _result(
                    entry,
                    "week",
                    "数量类型 1 按交货周归集",
                    amount=entry.quantity,
                    start=week_start(entry.requested_date),
                )
                details.append(entry)
        elif code == "jlr_deljit" and entry.code in {"3", "12", "79"}:
            _result(entry, "auxiliary", "累计及其他数量单列", amount=entry.quantity)
        else:
            _review(entry, f"数量类型待确认（原值 {entry.code or '空'}）")
    if cumulative:
        _calculate_deltas(cumulative, details, block, header, anchor)


def _calculate_deltas(
    cumulative: list[DraftEntry],
    details: list[DraftEntry],
    block: Source,
    header: Source,
    anchor: date,
) -> None:
    previous = _decimal(_value(block, "PreviousCumulativeQty"))
    baseline_raw = _value(header, "CumulativeQtyStartDate")
    baseline = _within_horizon(_date(baseline_raw, anchor), anchor)
    prior_cell = _cell(block, "PreviousCumulativeQty")
    previous_end: date | None = None
    cumulative.sort(key=lambda entry: (entry.earliest_date or date.min, entry.id))
    first = True
    with localcontext() as context:
        context.prec = 130
        for entry in cumulative:
            start, end = entry.earliest_date, entry.latest_date
            assert start is not None and end is not None
            if previous_end is not None and start <= previous_end:
                _review(entry, "累计需求区间重叠")
                previous = None
                previous_end = max(previous_end, end)
                continue
            overlap = any(
                detail.requested_date and start <= detail.requested_date <= end
                for detail in details
            )
            if first and (baseline_raw and (baseline is None or baseline >= start)):
                _review(entry, "累计基准日期无效")
            elif previous is None:
                _review(entry, "缺少有效的上一累计数量")
            elif overlap:
                _review(entry, "累计区间和明细日期重叠，需核对是否重复计量")
            else:
                used_details = (
                    [
                        row
                        for row in details
                        if row.requested_date
                        and row.requested_date < start
                        and (baseline is None or baseline <= row.requested_date)
                    ]
                    if first
                    else []
                )
                detail_total = sum((row.quantity for row in used_details), Decimal(0))
                delta = entry.quantity - previous - detail_total
                basis = f"本期累计 {entry.quantity} − 上期累计 {previous}"
                if prior_cell:
                    basis += f"（{prior_cell}）"
                if used_details:
                    detail_cells = "、".join(row.quantity_cell or "—" for row in used_details)
                    basis += f" − 已计入明细 {detail_total}（{detail_cells}）"
                if delta < 0:
                    _review(entry, "累计差分为负，需核对累计基准")
                elif start.weekday() == 0 and (end - start).days == 6:
                    _result(entry, "week", basis, amount=delta, start=start, end=end)
                else:
                    _result(
                        entry,
                        "interval",
                        basis,
                        amount=delta,
                        start=start,
                        end=end,
                        issue="月度或跨周区间保留总量，拆周口径待确认",
                    )
            first = False
            previous, previous_end, prior_cell = entry.quantity, end, entry.quantity_cell


def _source_entry(row: RowMapping, anchor: date) -> DraftEntry:
    fields: Source = row.source_fields
    actual_key = "DateTime" if "DateTime" in fields else "RequestedDeliveryDate"
    start_key = "EarliestDeliveryDate" if "EarliestDeliveryDate" in fields else "HorizonStartDate"
    end_key = "LastDeliveryDate" if "LastDeliveryDate" in fields else "HorizonEndDate"
    actual_raw = _value(fields, actual_key)
    start_raw, end_raw = _value(fields, start_key), _value(fields, end_key)
    actual = _within_horizon(row.requested_date or _date(actual_raw, anchor), anchor)
    start = _within_horizon(row.earliest_date or _date(start_raw, anchor), anchor)
    end = _within_horizon(row.latest_date or _date(end_raw, anchor), anchor)
    return DraftEntry(
        id=row.id,
        quantity=row.quantity,
        raw_quantity=_value(fields, "Qty"),
        code=_value(fields, "QtyType"),
        unit=_value(fields, "QtyUnit"),
        status_code=_value(fields, "DeliveryPlanStatusIndicator"),
        quantity_cell=_cell(fields, "Qty"),
        date_cell=_cell(fields, actual_key) if actual_raw else _cell(fields, start_key),
        end_date_cell=_cell(fields, end_key) if end_raw else None,
        requested_date=actual,
        earliest_date=start,
        latest_date=end,
        period="",
        state="confirmed_interval",
        plan=PlanQuantity(
            kind="review", basis="原报文", raw_start=actual_raw or start_raw, raw_end=end_raw
        ),
    )


def build_schedule_snapshot(connection: Connection, source: RowMapping) -> DraftSnapshot:
    code = cast(ScheduleCode, source.template)
    config = SCHEDULE_OUTPUTS[code]
    header: Source = source.source_fields
    report_date = _date(_value(header, config.timestamp), source.received_day)
    anchor: date = _within_horizon(report_date, source.received_day) or source.received_day
    blocks = (
        connection.execute(
            text("""
        SELECT id, source_fields FROM parsed_material_blocks WHERE run_id = :id ORDER BY position
    """),
            {"id": source.id},
        )
        .mappings()
        .all()
    )
    mapping_rows = connection.execute(
        text(f"""
        SELECT {_MAPPING_COLUMNS} FROM material_mappings
        WHERE customer = :customer AND customer_item = ANY(:items)
    """),
        {
            "customer": config.customer,
            "items": list(
                {
                    _value(block.source_fields, config.item)
                    for block in blocks
                    if _value(block.source_fields, config.item)
                }
            ),
        },
    ).mappings()
    mappings: dict[str, list[MaterialMapping]] = defaultdict(list)
    for row in mapping_rows:
        candidate = MaterialMapping.model_validate(row)
        mappings[candidate.customer_item].append(candidate)
    entries: dict[int, list[DraftEntry]] = defaultdict(list)
    for row in connection.execute(
        text("""
        SELECT q.id, q.block_id, q.quantity, q.source_fields,
               q.requested_date, q.earliest_date, q.latest_date
        FROM parsed_quantity_records q JOIN parsed_material_blocks b ON q.block_id = b.id
        WHERE b.run_id = :id ORDER BY b.position, q.position
    """),
        {"id": source.id},
    ).mappings():
        entries[row.block_id].append(_source_entry(row, anchor))

    materials: list[DraftMaterial] = []
    issues = 0
    for block in blocks:
        fields: Source = block.source_fields
        item = _value(fields, config.item)
        order = _value(fields, config.order) or _value(header, config.order)
        line = _value(fields, config.line)
        site = _value(fields, config.site) or _value(header, config.site)
        supplier = _value(fields, config.supplier)
        state, mapping = find_material_mapping(
            mappings[item or ""],
            customer=config.customer,
            item=item,
            order=order,
            order_line=line,
            ship_to=site,
            supplier=supplier,
        )
        lines = entries[block.id]
        _apply_schedule(code, lines, fields, header, anchor)
        issues += sum(1 for entry in lines if entry.plan and entry.plan.issue)
        materials.append(
            DraftMaterial(
                id=block.id,
                customer_item=item,
                customer_item_cell=_cell(fields, config.item),
                order_no=order,
                order_line=line,
                ship_to=site,
                supplier_item=supplier,
                mapping_state=state,
                mapping=mapping,
                entries=lines,
            )
        )
    return DraftSnapshot(
        template=code,
        rule_version=config.version(code),
        output_version=config.version(code),
        parser_version=source.parser_version,
        attachment_id=source.attachment_id,
        mail_id=source.mail_id,
        filename=source.filename,
        document_no=_value(header, config.number),
        document_date=report_date.isoformat() if report_date else None,
        materials=materials,
        pending_entries=issues,
        pending_mappings=sum(material.mapping_state != "matched" for material in materials),
    )
