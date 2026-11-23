from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from relay.parsers.document import (
    MaterialBlock,
    ParsedDocument,
    ParseError,
    QuantityRecord,
    SourceValue,
    WorkbookRow,
    WorkbookRows,
    adjacent_fields,
    decimal_value,
    fixed_value,
    labels,
    load_rows,
    parsed_date,
)
from relay.parsers.vitesco import parse_vitesco_rows

_DELFOR_HEADER = frozenset(
    {
        "DelforNo",
        "DelforType",
        "DelforCreateDate",
        "HorizonStartDate",
        "HorizonEndDate",
        "SupplierNo",
        "ShipFromNo",
        "BuyerNo",
        "ShipToNo",
        "MaterialIssuerNo",
        "PreviousDeliveryScheduleNo",
        "ScheduleIssuerNo",
        "CumulativeQtyStartDate",
    }
)
_DELJIT_HEADER = frozenset(
    {
        "DeljitNo",
        "DeljitType",
        "DeljitCreateDate",
        "HorizonStartDate",
        "HorizonEndDate",
        "SupplierNo",
        "ShipFromNo",
        "BuyerNo",
        "ShipToNo",
        "PreviousDeliveryScheduleNo",
        "ScheduleIssuerNo",
        "IntermediateConsigneeNo",
    }
)
_MATERIAL = frozenset({"poNo", "POLineNo", "BuyerItemNo", "SupplierItemNumber"})
_KRO_HEADER = frozenset(
    {
        "CustomNo",
        "SupplierNo",
        "NewTransmissionNo",
        "OldTransmissionNo",
        "TransferDate",
    }
)
_KRO_MATERIAL = frozenset(
    {
        "OrderNo",
        "CustomerItemNo",
        "SupplierItemNo",
        "CustomerPlant",
        "DeliveryInterval",
        "LastDeliveryDate",
        "NewDeliveryInstructionsNo",
        "OldDeliveryInstructionsNo",
        "NewDeliveryInstructionsDate",
        "OldDeliveryInstructionsDate",
        "UnloadingPoint",
        "UsageKey",
        "LastDeliveryQty",
        "CustomeReference",
        "DestinationPlace",
        "ConsumptionPoint",
        "CumulativeQty",
        "LastEntryDate",
        "LastReceiptQty",
        "QuantityUnit",
        "LastDeliveryNo",
        "Text",
    }
)
_QTY_BASE = {"Qty", "QtyType", "QtyUnit", "RequestedDeliveryDate"}
_KRO_QTY = {"DateTime", "Qty"}


@dataclass(frozen=True)
class ScheduleTemplate:
    code: str
    title: str
    number: str
    header: frozenset[str]
    short_dates: bool
    interval: bool


_SCHEDULES = (
    ScheduleTemplate("drx_delfor", "DRX-DELFOR", "DelforNo", _DELFOR_HEADER, False, True),
    ScheduleTemplate("jlr_delfor", "JLR_DELFOR", "DelforNo", _DELFOR_HEADER, True, True),
    ScheduleTemplate("jlr_deljit", "JLR_DELJIT", "DeljitNo", _DELJIT_HEADER, True, False),
)


def _collect_fields(rows: WorkbookRows, keys: frozenset[str]) -> dict[str, SourceValue]:
    result: dict[str, SourceValue] = {}
    for row in rows:
        result.update(adjacent_fields(row, labels(row), keys))
    return result


def _blocks(rows: WorkbookRows, starts: list[int]) -> Iterator[WorkbookRows]:
    for index, start in enumerate(starts):
        end = starts[index + 1] if index + 1 < len(starts) else len(rows)
        yield rows[start:end]


def _row_empty(row: WorkbookRow) -> bool:
    return all(cell.value is None or cell.value == "" for cell in row)


def _table(rows: WorkbookRows, required: set[str]) -> tuple[int, dict[str, int]]:
    match = [
        (index, columns)
        for index, row in enumerate(rows)
        if required.issubset(columns := labels(row))
    ]
    if len(match) != 1:
        raise ParseError("Expected exactly one quantity header per material block")
    return match[0]


def _attributes(rows: WorkbookRows) -> dict[str, SourceValue]:
    values: dict[str, SourceValue] = {}
    for header, data in zip(rows, rows[1:], strict=False):
        if not _row_empty(header) and not _row_empty(data):
            values.update({key: fixed_value(data[col]) for key, col in labels(header).items()})
            break
    return values


def _schedule_qty(
    row: WorkbookRow, columns: dict[str, int], template: ScheduleTemplate
) -> QuantityRecord:
    source = {name: fixed_value(row[column]) for name, column in columns.items()}
    requested = parsed_date(source["RequestedDeliveryDate"], keep_short=template.short_dates)
    first = last = None
    if template.interval:
        start = source["HorizonStartDate"]
        end = source["HorizonEndDate"]
        if bool(start.value) != bool(end.value):
            raise ParseError(f"{start.cell}: missing delivery interval endpoint")
        first = parsed_date(start, keep_short=template.short_dates)
        last = parsed_date(end, keep_short=template.short_dates)
        if first and last and first > last:
            raise ParseError(f"{start.cell}: inverted delivery interval")
    return QuantityRecord(decimal_value(source["Qty"]), requested, first, last, source)


def _parse_schedule(rows: WorkbookRows, template: ScheduleTemplate) -> ParsedDocument:
    starts = [
        index for index, row in enumerate(rows) if {"poNo", "BuyerItemNo"}.issubset(labels(row))
    ]
    if not starts:
        raise ParseError("Missing schedule material block")
    header = _collect_fields(rows[: starts[0]], template.header)
    if template.number not in header or not header[template.number].value:
        raise ParseError("Missing schedule document number")
    required = (
        _QTY_BASE | {"HorizonStartDate", "HorizonEndDate"} if template.interval else _QTY_BASE
    )
    blocks: list[MaterialBlock] = []
    for group in _blocks(rows, starts):
        index, columns = _table(group, required)
        fields = adjacent_fields(group[0], labels(group[0]), _MATERIAL)
        if not fields["BuyerItemNo"].value:
            raise ParseError(f"{fields['BuyerItemNo'].cell}: missing material number")
        fields.update(_attributes(group[1:index]))
        quantities = tuple(
            _schedule_qty(row, columns, template)
            for row in group[index + 1 :]
            if not _row_empty(row)
        )
        if not quantities:
            raise ParseError("Material block has no quantity rows")
        blocks.append(MaterialBlock(fields, quantities))
    return ParsedDocument(header, tuple(blocks), template.code)


def _parse_kroschu(rows: WorkbookRows) -> ParsedDocument:
    starts = [index for index, row in enumerate(rows) if "OrderNo" in labels(row)]
    if not starts:
        raise ParseError("Missing Kroschu material block")
    header = _collect_fields(rows[: starts[0]], _KRO_HEADER)
    if "NewTransmissionNo" not in header or not header["NewTransmissionNo"].value:
        raise ParseError("Missing Kroschu transmission number")
    blocks: list[MaterialBlock] = []
    for group in _blocks(rows, starts):
        index, columns = _table(group, _KRO_QTY)
        fields = _collect_fields(group[:index], _KRO_MATERIAL)
        if "CustomerItemNo" not in fields or not fields["CustomerItemNo"].value:
            raise ParseError("Missing Kroschu customer material number")
        if "QuantityUnit" not in fields or not fields["QuantityUnit"].value:
            raise ParseError("Missing Kroschu quantity unit")
        quantities = []
        for row in group[index + 1 :]:
            if _row_empty(row):
                continue
            if "Pacage Info" in labels(row):
                break
            source = {name: fixed_value(row[column]) for name, column in columns.items()}
            raw_date = source["DateTime"]
            if raw_date.value is None:
                raise ParseError(f"{raw_date.cell}: missing delivery date")
            requested = (
                parsed_date(raw_date, keep_short=True) if raw_date.value != "000000" else None
            )
            source["QtyUnit"] = fields["QuantityUnit"]
            quantities.append(
                QuantityRecord(decimal_value(source["Qty"]), requested, None, None, source)
            )
        if not quantities:
            raise ParseError("Kroschu material block has no quantity rows")
        blocks.append(MaterialBlock(fields, tuple(quantities)))
    return ParsedDocument(header, tuple(blocks), "kroschu_4905")


def parse_workbook(path: Path) -> ParsedDocument:
    rows = load_rows(path)
    headings = tuple(
        cell.value.strip() for row in rows[:8] for cell in row if isinstance(cell.value, str)
    )
    if any(value.startswith("Vitesco-DELFOR") for value in headings):
        return parse_vitesco_rows(rows)
    if any(value.startswith("4905 - Delivery Call Off") for value in headings):
        return _parse_kroschu(rows)
    for template in _SCHEDULES:
        if any(value.startswith(template.title) for value in headings):
            return _parse_schedule(rows, template)
    raise ParseError("Unrecognized schedule workbook title")
