from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.utils.exceptions import InvalidFileException

_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_EXPANDED_BYTES = 64 * 1024 * 1024
_MAX_PARTS = 512
_MAX_ROWS = 5000
_MAX_COLUMNS = 80

_HEADER_FIELDS = frozenset(
    {
        "DelforNo",
        "DelforType",
        "DelforCreateDate",
        "PreviousDeliveryNo",
        "MessageFunction",
        "CustomerReleaseDate",
        "BuyerNo",
        "SupplierNo",
        "BlanketOrderNo",
    }
)
_BLOCK_FIELDS = frozenset({"LineItemNo", "BuyerItemNo", "ShipToNo", "ActionCode"})
_BLOCK_COLUMNS = frozenset(
    {"WarehousePlace", "DischargePlace", "SellerItemNo", "ItemDes", "CumReceivedQty", "ReceivedQty"}
)
_QUANTITY_FIELDS = frozenset(
    {
        "DeliveryPlanStatusIndicator",
        "QtyType",
        "Qty",
        "QtyUnit",
        "RequestedDeliveryDate",
        "EarliestDeliveryDate",
        "LastDeliveryDate",
    }
)


class ParseError(ValueError):
    """Workbook does not match the supported template or is unsafe to process."""


@dataclass(frozen=True)
class SourceValue:
    value: str | None
    cell: str


@dataclass(frozen=True)
class QuantityRecord:
    quantity: Decimal
    requested_date: date | None
    earliest_date: date | None
    latest_date: date | None
    source: dict[str, SourceValue]


@dataclass(frozen=True)
class MaterialBlock:
    fields: dict[str, SourceValue]
    quantities: tuple[QuantityRecord, ...]


@dataclass(frozen=True)
class VitescoDocument:
    fields: dict[str, SourceValue]
    blocks: tuple[MaterialBlock, ...]


def _value(cell: Cell | MergedCell) -> SourceValue:
    value = cell.value
    return SourceValue(
        None if value is None else str(value), f"{cell.parent.title}!{cell.coordinate}"
    )


def _label(cell: Cell | MergedCell) -> str:
    if not isinstance(cell.value, str):
        return ""
    first, *_ = cell.value.splitlines() or [""]
    return first.removesuffix("_x000D_").strip()


def _date(field: SourceValue) -> date | None:
    if field.value is None:
        return None
    raw = field.value.strip()
    try:
        if len(raw) == 8 and raw.isascii() and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d").date()
        if len(raw) == 10:
            return date.fromisoformat(raw)
        if len(raw) == 19 and raw.endswith(" 00:00:00"):
            return date.fromisoformat(raw[:10])
    except ValueError as error:
        raise ParseError(f"{field.cell}: invalid delivery date") from error
    raise ParseError(f"{field.cell}: unsupported delivery date")


def _quantity(row: tuple[Cell | MergedCell, ...], columns: dict[str, int]) -> QuantityRecord:
    source = {field: _value(row[index]) for field, index in columns.items()}
    raw = source["Qty"]
    try:
        amount = Decimal(raw.value or "")
    except InvalidOperation as error:
        raise ParseError(f"{raw.cell}: invalid quantity") from error
    if not amount.is_finite():
        raise ParseError(f"{raw.cell}: quantity must be finite")
    start = _date(source["EarliestDeliveryDate"])
    end = _date(source["LastDeliveryDate"])
    if (start is None) != (end is None) or (start and end and start > end):
        raise ParseError(f"{source['EarliestDeliveryDate'].cell}: invalid delivery interval")
    return QuantityRecord(amount, _date(source["RequestedDeliveryDate"]), start, end, source)


def _adjacent_fields(
    row: tuple[Cell | MergedCell, ...], labels: dict[str, int], keys: frozenset[str]
) -> dict[str, SourceValue]:
    return {key: _value(row[labels[key] + 1]) for key in keys.intersection(labels)}


def _verify_archive(path: Path) -> None:
    if path.suffix.lower() != ".xlsx" or path.stat().st_size > _MAX_FILE_BYTES:
        raise ParseError("Expected an XLSX file within the size limit")
    with ZipFile(path) as archive:
        parts = archive.infolist()
        if len(parts) > _MAX_PARTS or sum(item.file_size for item in parts) > _MAX_EXPANDED_BYTES:
            raise ParseError("Workbook exceeds archive limits")


def parse_vitesco(path: Path) -> VitescoDocument:
    try:
        _verify_archive(path)
        workbook = load_workbook(path, keep_links=False)
    except (BadZipFile, InvalidFileException, KeyError) as error:
        raise ParseError("Invalid XLSX package") from error

    try:
        if len(workbook.worksheets) != 1:
            raise ParseError("Expected one worksheet")
        sheet = workbook.worksheets[0]
        if sheet.max_row > _MAX_ROWS or sheet.max_column > _MAX_COLUMNS:
            raise ParseError("Worksheet exceeds row or column limits")
        return _parse_rows(list(sheet.iter_rows(max_col=sheet.max_column + 1)))
    finally:
        workbook.close()


def _parse_rows(rows: list[tuple[Cell | MergedCell, ...]]) -> VitescoDocument:
    if not any(
        isinstance(cell.value, str) and cell.value.strip().startswith("Vitesco-DELFOR")
        for row in rows[:10]
        for cell in row
    ):
        raise ParseError("Expected the Vitesco DELFOR document title")

    fields: dict[str, SourceValue] = {}
    blocks: list[MaterialBlock] = []
    block_fields: dict[str, SourceValue] = {}
    records: list[QuantityRecord] = []
    columns: dict[str, int] = {}

    for row_number, row in enumerate(rows, start=1):
        if any(cell.data_type == "f" for cell in row):
            raise ParseError(f"{row[0].parent.title}!{row[0].row}: unexpected formula")
        labels = {label: index for index, cell in enumerate(row[:-1]) if (label := _label(cell))}
        if {"LineItemNo", "BuyerItemNo"}.issubset(labels):
            if block_fields:
                blocks.append(_finish_block(block_fields, records))
            block_fields = _adjacent_fields(row, labels, _BLOCK_FIELDS)
            columns = {}
            records = []
        elif block_fields and not columns and "WarehousePlace" in labels:
            if row_number >= len(rows):
                raise ParseError("Missing material attributes beneath the block header")
            attributes = rows[row_number]
            block_fields.update(
                {
                    key: _value(attributes[labels[key]])
                    for key in _BLOCK_COLUMNS.intersection(labels)
                }
            )
        elif _QUANTITY_FIELDS.issubset(labels) and block_fields:
            if columns:
                raise ParseError("Duplicate quantity header in the material block")
            columns = {key: index for key, index in labels.items() if key in _QUANTITY_FIELDS}
        elif columns:
            if any(cell.value is not None for cell in row):
                records.append(_quantity(row, columns))
        elif not block_fields:
            fields.update(_adjacent_fields(row, labels, _HEADER_FIELDS))

    if block_fields:
        blocks.append(_finish_block(block_fields, records))
    if not blocks or not fields.get("DelforNo") or not fields["DelforNo"].value:
        raise ParseError("Missing document number or material block")
    return VitescoDocument(fields, tuple(blocks))


def _finish_block(fields: dict[str, SourceValue], records: list[QuantityRecord]) -> MaterialBlock:
    if not fields["BuyerItemNo"].value or not records:
        raise ParseError(f"{fields['BuyerItemNo'].cell}: missing material number or quantity rows")
    return MaterialBlock(fields, tuple(records))
