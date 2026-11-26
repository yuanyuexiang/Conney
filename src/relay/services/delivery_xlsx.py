from collections import defaultdict
from datetime import date
from decimal import Decimal, localcontext
from io import BytesIO
from typing import Literal
from zoneinfo import ZoneInfo

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from relay.services.delivery import DraftSnapshot

_BLUE = "075985"
_HEADERS = "E9F3FA"
_DATE_FORMAT = "yyyy-mm-dd"
_NUMBER_FORMAT = "#,##0.###############"


def _write_text(sheet: Worksheet, row: int, column: int, value: str | None) -> None:
    if value is not None:
        # Business fields can start with '='. Only our own fixed formulas are executable.
        cell = sheet.cell(row, column, value)
        cell.data_type = "s"


def _header(sheet: Worksheet, row: int) -> None:
    for cell in sheet[row]:
        cell.fill = PatternFill("solid", fgColor=_HEADERS)
        cell.font = Font(bold=True, color=_BLUE)
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    sheet.row_dimensions[row].height = 30


def _add_quantity(left: Decimal, right: Decimal) -> Decimal:
    # The input validator allows 61 integer places and 60 fractional places.
    with localcontext(prec=130):
        return left + right


def _write_quantity(sheet: Worksheet, row: int, column: int, amount: Decimal) -> None:
    digits = "".join(map(str, amount.as_tuple().digits)).rstrip("0")
    if len(digits) > 15:
        _write_text(sheet, row, column, format(amount, "f"))
        return
    adjusted = amount.adjusted()
    number_format = _NUMBER_FORMAT
    if amount != 0 and (adjusted < -8 or adjusted > 15):
        number_format = "0.##############E+00"
    elif amount == amount.to_integral_value():
        number_format = "#,##0"
    sheet.cell(row, column, amount).number_format = number_format


def _production_sheet(sheet: Worksheet, draft: DraftSnapshot) -> None:
    sheet.title = "给生产"
    sheet.append(["德科交付计划 · 草稿", None, f"报文 {draft.document_no or '—'}"])
    sheet["A1"].font = Font(bold=True, color=_BLUE, size=14)
    sheet["C1"].font = Font(color=_BLUE)
    sheet.append(
        [
            "匹配状态",
            "最小包装量",
            "康尼料号",
            "装箱数",
            "每托箱数",
            "每托数量",
            "图号",
            "供应商料号",
            "客户料号",
            "采购订单",
            "安全库存水平",
        ]
    )
    sheet["K3"] = "发货时间"
    sheet["K4"] = "运输方式"
    sheet.freeze_panes = "L5"
    for index, width in enumerate((14, 13, 21, 12, 12, 14, 19, 21, 18, 20, 15), start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    quantities: dict[tuple[date, Literal["air", "sea"], date | None], dict[int, Decimal]] = (
        defaultdict(dict)
    )
    for row, material in enumerate(draft.materials, start=5):
        mapping = material.mapping if material.mapping_state == "matched" else None
        sheet.cell(row, 1, "已匹配" if mapping else "待核对")
        for column, value in (
            (8, material.supplier_item),
            (9, material.customer_item),
            (10, material.order_no),
        ):
            _write_text(sheet, row, column, value)
        if mapping is None:
            continue
        for column, number in (
            (2, mapping.minimum_pack),
            (4, mapping.units_per_carton),
            (5, mapping.cartons_per_pallet),
            (11, mapping.safety_stock),
        ):
            if number is not None:
                sheet.cell(row, column, number).number_format = _NUMBER_FORMAT
        _write_text(sheet, row, 3, mapping.internal_item)
        _write_text(sheet, row, 7, mapping.drawing)
        if mapping.units_per_carton is not None and mapping.cartons_per_pallet is not None:
            sheet.cell(row, 6, f"=D{row}*E{row}").number_format = _NUMBER_FORMAT
        for entry in material.entries:
            if entry.shipment is not None:
                shipment = entry.shipment
                key = (shipment.ship_date, shipment.mode, shipment.manual_eta)
                quantities[key][row] = _add_quantity(
                    quantities[key].get(row, Decimal(0)), entry.quantity
                )

    eta_row = 5 + len(draft.materials)
    sheet.cell(eta_row, 10, "ETA")
    sheet.cell(eta_row + 1, 10, "到货周")
    for column, (key, amounts) in enumerate(
        sorted(
            quantities.items(), key=lambda item: (item[0][0], item[0][1], item[0][2] or date.min)
        ),
        start=12,
    ):
        ship_date, mode, override = key
        letter = get_column_letter(column)
        sheet.column_dimensions[letter].width = 16
        sheet.cell(2, column, f'=YEAR({letter}3)&"W"&TEXT(WEEKNUM({letter}3,2),"00")')
        sheet.cell(3, column, ship_date).number_format = _DATE_FORMAT
        sheet.cell(4, column, "空运" if mode == "air" else "海运")
        eta = sheet.cell(eta_row, column, override or f"={letter}3+{14 if mode == 'air' else 84}")
        eta.number_format = _DATE_FORMAT
        sheet.cell(
            eta_row + 1,
            column,
            f'=YEAR({letter}{eta_row})&"W"&TEXT(WEEKNUM({letter}{eta_row},2),"00")',
        )
        for row, quantity in amounts.items():
            _write_quantity(sheet, row, column, quantity)
    _header(sheet, 2)


def _source_sheet(sheet: Worksheet, draft: DraftSnapshot) -> None:
    sheet.append(["来源附件"])
    _write_text(sheet, 1, 2, draft.filename)
    sheet.append(["报文编号"])
    _write_text(sheet, 2, 2, draft.document_no)
    sheet.append(["报文日期"])
    _write_text(sheet, 3, 2, draft.document_date)
    sheet.append(["规则版本", draft.rule_version])
    headers = [
        "客户料号",
        "采购订单",
        "康尼料号",
        "原始数量",
        "单位",
        "数量类型",
        "状态代码",
        "要求交货日",
        "区间开始",
        "区间结束",
        "数量单元格",
        "日期单元格",
        "核对状态",
        "发货日期",
        "运输方式",
        "ETA",
        "人工 ETA",
        "发货依据",
        "录入人",
        "映射版本",
        "核对结论",
        "确认交货日",
        "核对依据",
        "核对人",
        "核对时间（北京时间）",
    ]
    sheet.append(headers)
    states = {
        "scheduled": "已安排发货",
        "recorded_zero": "零需求",
        "transport_missing": "待补运输信息",
        "period_review": "区间待核对",
        "code_review": "代码待核对",
        "confirmed_interval": "已确认区间预测",
        "excluded": "已排除",
    }
    decisions = {
        "include": "计入客户需求",
        "exclude": "从客户需求排除",
        "set_date": "确认交货日",
        "keep_interval": "保留为区间预测",
    }
    shanghai = ZoneInfo("Asia/Shanghai")
    for material in draft.materials:
        for entry in material.entries:
            shipment = entry.shipment
            mapping = material.mapping
            review = entry.review
            state = (
                "区间预测"
                if entry.state == "confirmed_interval" and not review
                else states[entry.state]
            )
            row = sheet.max_row + 1
            for column, value in (
                (1, material.customer_item),
                (2, material.order_no),
                (
                    3,
                    mapping.internal_item
                    if material.mapping_state == "matched" and mapping
                    else None,
                ),
                (4, entry.raw_quantity or format(entry.quantity, "f")),
                (5, entry.unit),
                (6, entry.code),
                (7, entry.status_code),
                (11, entry.quantity_cell),
                (12, " / ".join(spot for spot in (entry.date_cell, entry.end_date_cell) if spot)),
                (13, state if material.mapping_state == "matched" else "物料待核对"),
                (15, ("空运" if shipment.mode == "air" else "海运") if shipment else None),
                (18, shipment.source_note if shipment else None),
                (19, shipment.entered_by if shipment else None),
                (20, f"#{mapping.id} / v{mapping.revision}" if mapping else None),
                (21, decisions[review.action] if review else None),
                (23, review.source_note if review else None),
                (24, review.reviewed_by if review else None),
                (
                    25,
                    review.reviewed_at.astimezone(shanghai).isoformat(timespec="seconds")
                    if review
                    else None,
                ),
            ):
                _write_text(sheet, row, column, value)
            for column, day in (
                (8, entry.requested_date),
                (9, entry.earliest_date),
                (10, entry.latest_date),
                (14, shipment.ship_date if shipment else None),
                (16, entry.eta),
                (17, shipment.manual_eta if shipment else None),
                (22, review.delivery_date if review else None),
            ):
                if day is not None:
                    sheet.cell(row, column, day).number_format = _DATE_FORMAT
    _header(sheet, 5)
    sheet.freeze_panes = "D6"
    sheet.auto_filter.ref = f"A5:Y{sheet.max_row}"
    for column in range(1, len(headers) + 1):
        sheet.column_dimensions[get_column_letter(column)].width = 17
    sheet.column_dimensions["B"].width = 24
    sheet.column_dimensions["R"].width = 34
    sheet.column_dimensions["W"].width = 34
    sheet.column_dimensions["Y"].width = 26


def build_workbook(draft: DraftSnapshot) -> bytes:
    workbook = Workbook()
    _production_sheet(workbook["Sheet"], draft)
    _source_sheet(workbook.create_sheet("需求与来源"), draft)
    with BytesIO() as content:
        workbook.save(content)
        return content.getvalue()
