from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from io import BytesIO
from typing import cast

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from relay.services.delivery import DraftEntry, DraftMaterial, DraftSnapshot
from relay.services.delivery_xlsx import _add_quantity, _header, _write_quantity, _write_text
from relay.services.schedule_delivery import (
    KROSCHU_TRANSIT_DAYS,
    SCHEDULE_OUTPUTS,
    ScheduleCode,
    week_start,
)

_DATE_FORMAT = "yyyy-mm-dd"
_KEY_LABELS = ("康尼料号", "客户料号", "采购订单", "订单行", "收货方", "图号", "单位")
_AXIS_COLUMN = len(_KEY_LABELS) + 1
_ISSUE_COLOR = PatternFill("solid", fgColor="FFF3DA")


def _metadata(sheet: Worksheet, draft: DraftSnapshot, title: str) -> None:
    sheet.cell(1, 1, title).font = Font(bold=True, size=14, color="075985")
    sheet.cell(2, 1, "附件")
    sheet.merge_cells("B2:D2")
    _write_text(sheet, 2, 2, draft.filename)
    sheet.cell(2, 5, "报文")
    sheet.merge_cells("F2:G2")
    _write_text(sheet, 2, 6, draft.document_no)
    sheet.cell(2, 8, "规则 / 输出版本")
    sheet.merge_cells("I2:L2")
    _write_text(sheet, 2, 9, f"{draft.rule_version} / {draft.output_version}")
    sheet.cell(2, 13, "解析版本")
    _write_text(sheet, 2, 14, draft.parser_version)


def _widths(sheet: Worksheet, widths: tuple[int, ...]) -> None:
    for col, width in enumerate(widths, 1):
        sheet.column_dimensions[get_column_letter(col)].width = width


def _mapped_item(material: DraftMaterial) -> str | None:
    return (
        material.mapping.internal_item
        if material.mapping_state == "matched" and material.mapping
        else None
    )


def _keys(material: DraftMaterial, unit: str) -> tuple[str | None, ...]:
    return (
        _mapped_item(material),
        material.customer_item,
        material.order_no,
        material.order_line,
        material.ship_to,
        material.mapping.drawing if _mapped_item(material) and material.mapping else None,
        unit,
    )


def _weekly_axis(values: list[date]) -> list[date]:
    if not values:
        return []
    first, last = min(values), max(values)
    return [first + timedelta(days=index) for index in range(0, (last - first).days + 1, 7)]


def _schedule_rows(draft: DraftSnapshot) -> list[tuple[DraftMaterial, list[DraftEntry]]]:
    rows: dict[object, tuple[DraftMaterial, list[DraftEntry]]] = {}
    for material in draft.materials:
        key: object = material.id
        if draft.template == "kroschu_4905" and material.customer_item and material.order_no:
            key = (
                material.customer_item,
                material.order_no,
                material.ship_to,
                material.supplier_item,
                material.order_line,
            )
        if key not in rows:
            rows[key] = (material, [])
        rows[key][1].extend(material.entries)
    return list(rows.values())


def _matrix(
    sheet: Worksheet,
    draft: DraftSnapshot,
    unit: str,
    axis_mode: str,
) -> list[date]:
    rows = _schedule_rows(draft)
    amounts: dict[int, dict[date, Decimal]] = defaultdict(dict)
    for row_no, (_, entries) in enumerate(rows, 5):
        for entry in entries:
            plan = entry.plan
            if (
                not plan
                or plan.kind not in {"day", "week"}
                or plan.amount is None
                or not plan.start
            ):
                continue
            day = plan.start
            if axis_mode == "week":
                day = week_start(day)
            if axis_mode == "month":
                day = (day - timedelta(days=KROSCHU_TRANSIT_DAYS)).replace(day=1)
            amounts[row_no][day] = _add_quantity(amounts[row_no].get(day, Decimal(0)), plan.amount)
    axis = sorted({day for values in amounts.values() for day in values})
    if axis_mode == "week":
        axis = _weekly_axis(axis)
    _metadata(sheet, draft, sheet.title)
    for col, key_label in enumerate(_KEY_LABELS, 1):
        sheet.cell(3, col, key_label)
    if draft.template == "kroschu_4905" and axis_mode != "month":
        sheet.cell(4, 7, "ETD")
    elif axis_mode == "week":
        sheet.cell(4, 7, "ISO 周")
    else:
        sheet.cell(4, 7, "ETD 月份")
    for col, day in enumerate(axis, _AXIS_COLUMN):
        letter = get_column_letter(col)
        sheet.column_dimensions[letter].width = 14
        sheet.cell(3, col, day).number_format = "yyyy-mm" if axis_mode == "month" else _DATE_FORMAT
        if draft.template == "kroschu_4905" and axis_mode != "month":
            sheet.cell(4, col, f"={letter}3-{KROSCHU_TRANSIT_DAYS}").number_format = _DATE_FORMAT
        elif axis_mode == "week":
            iso = day.isocalendar()
            sheet.cell(4, col, f"{iso.year}-W{iso.week:02d}")
    row_axis = {day: col for col, day in enumerate(axis, _AXIS_COLUMN)}
    for row_no, (material, _) in enumerate(rows, 5):
        for col, value in enumerate(_keys(material, unit), 1):
            _write_text(sheet, row_no, col, value)
        if material.mapping_state != "matched":
            sheet.cell(row_no, 1).fill = _ISSUE_COLOR
        for day, amount in amounts.get(row_no, {}).items():
            _write_quantity(sheet, row_no, row_axis[day], amount)
    _header(sheet, 3)
    _widths(sheet, (20, 23, 20, 12, 15, 24, 12))
    sheet.freeze_panes = "H5"
    return axis


def _traces(sheet: Worksheet, draft: DraftSnapshot, *, review: bool = False) -> None:
    _metadata(sheet, draft, sheet.title)
    sheet.append(
        [
            "康尼料号",
            "客户料号",
            "采购订单",
            "订单行",
            "收货方",
            "供应商料号",
            "匹配结果",
            "映射版本",
            "原始日期/区间开始",
            "原始区间结束",
            "数量类型",
            "原始数量",
            "单位",
            "计划起始",
            "计划结束",
            "计算数量",
            "处理口径",
            "核对事项",
            "数量坐标",
            "日期坐标",
            "结束坐标",
        ]
    )
    mapping_notes = {
        "missing": "未匹配康尼料号",
        "ambiguous": "存在多个同级匹配",
        "supplier_mismatch": "供应商料号与映射不符",
    }
    for material in draft.materials:
        mapping_issue = mapping_notes.get(material.mapping_state)
        for entry in material.entries:
            plan = entry.plan
            if plan is None or (review and not plan.issue and not mapping_issue):
                continue
            issue = "；".join(part for part in (mapping_issue, plan.issue) if part)
            row = sheet.max_row + 1
            values = {
                1: _mapped_item(material),
                2: material.customer_item,
                3: material.order_no,
                4: material.order_line,
                5: material.ship_to,
                6: material.supplier_item,
                7: mapping_issue or "已匹配",
                8: (
                    f"#{material.mapping.id} / {material.mapping.revision}"
                    if material.mapping
                    else None
                ),
                9: plan.raw_start,
                10: plan.raw_end,
                11: entry.code,
                12: entry.raw_quantity
                if entry.raw_quantity is not None
                else format(entry.quantity, "f"),
                13: entry.unit,
                17: plan.basis,
                18: issue or None,
                19: entry.quantity_cell,
                20: entry.date_cell,
                21: entry.end_date_cell,
            }
            for column, value in values.items():
                _write_text(sheet, row, column, value)
            if len(plan.basis) > 34 or issue:
                sheet.cell(row, 17).alignment = Alignment(wrap_text=True, vertical="center")
                sheet.cell(row, 18).alignment = Alignment(wrap_text=True, vertical="center")
                sheet.row_dimensions[row].height = 36
            if plan.start:
                sheet.cell(row, 14, plan.start).number_format = _DATE_FORMAT
            if plan.end:
                sheet.cell(row, 15, plan.end).number_format = _DATE_FORMAT
            if plan.amount is not None:
                _write_quantity(sheet, row, 16, plan.amount)
    _header(sheet, 3)
    _widths(
        sheet, (19, 23, 20, 11, 13, 18, 23, 16, 20, 20, 12, 16, 10, 15, 15, 15, 52, 36, 18, 18, 18)
    )
    sheet.freeze_panes = "D4"
    sheet.auto_filter.ref = sheet.dimensions.replace("A1", "A3", 1)


def _inventory(sheet: Worksheet, draft: DraftSnapshot, axis: list[date]) -> None:
    _metadata(sheet, draft, "Vitesco 库存测算")
    sheet.cell(3, 1, "填写黄色单元格。库存按上期库存减本期预测、加本期实际产量计算。")
    for col, label in enumerate(("康尼料号", "客户料号", "采购订单", "项目", "期初库存"), 1):
        sheet.cell(4, col, label)
    for offset, day in enumerate(axis, 6):
        sheet.cell(4, offset, day).number_format = _DATE_FORMAT
        sheet.column_dimensions[get_column_letter(offset)].width = 14
    for index, material in enumerate(draft.materials):
        top = 5 + index * 4
        for col, value in enumerate(
            (_mapped_item(material), material.customer_item, material.order_no), 1
        ):
            _write_text(sheet, top, col, value)
        for offset, name in enumerate(("客户预测", "实际产量", "期末库存", "最大产能")):
            sheet.cell(top + offset, 4, name)
        sheet.cell(top + 2, 5).fill = _ISSUE_COLOR
        for col in range(6, 6 + len(axis)):
            letter = get_column_letter(col)
            previous = get_column_letter(col - 1)
            forecast_cell = f"'交货预测周表'!{get_column_letter(col + 2)}{index + 5}"
            sheet.cell(top, col, f'=IF(ISNUMBER({forecast_cell}),{forecast_cell},"")')
            sheet.cell(top + 1, col).fill = _ISSUE_COLOR
            sheet.cell(top + 3, col).fill = _ISSUE_COLOR
            inputs = f"{previous}{top + 2},{letter}{top},{letter}{top + 1}"
            sheet.cell(
                top + 2,
                col,
                f'=IF(COUNT({inputs})=3,{previous}{top + 2}-{letter}{top}+{letter}{top + 1},"")',
            )
    _header(sheet, 4)
    _widths(sheet, (21, 23, 20, 15, 14))
    sheet.freeze_panes = "F5"


def build_schedule_workbook(draft: DraftSnapshot) -> bytes:
    config = SCHEDULE_OUTPUTS[cast(ScheduleCode, draft.template)]
    workbook = Workbook()
    first = workbook["Sheet"]
    first.title = config.sheets[0]
    if draft.template == "kroschu_4905":
        _matrix(first, draft, config.unit, "day")
        _matrix(workbook.create_sheet(config.sheets[1]), draft, config.unit, "week")
        _matrix(workbook.create_sheet(config.sheets[2]), draft, config.unit, "month")
    else:
        axis = _matrix(first, draft, config.unit, "week")
        if draft.template == "vitesco_delfor":
            _inventory(workbook.create_sheet(config.sheets[1]), draft, axis)
    _traces(workbook.create_sheet("需求与来源"), draft)
    _traces(workbook.create_sheet("待核对"), draft, review=True)
    with BytesIO() as output:
        workbook.save(output)
        return output.getvalue()
