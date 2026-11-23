from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from openpyxl import load_workbook
from openpyxl.cell.cell import Cell, MergedCell
from openpyxl.utils.exceptions import InvalidFileException

WorkbookRow = tuple[Cell | MergedCell, ...]
WorkbookRows = list[WorkbookRow]

_MAX_FILE_BYTES = 20 * 1024 * 1024
_MAX_EXPANDED_BYTES = 64 * 1024 * 1024
_MAX_PARTS = 512
_MAX_ROWS = 5000
_MAX_COLUMNS = 80


class ParseError(ValueError):
    """Workbook does not match the supported template or is unsafe to process."""

    def __init__(self, reason: str, *, location: str | None = None) -> None:
        super().__init__(f"{location}: {reason}" if location else reason)
        self.location = location
        self.reason = reason


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
class ParsedDocument:
    fields: dict[str, SourceValue]
    blocks: tuple[MaterialBlock, ...]
    template: str


def fixed_value(cell: Cell | MergedCell) -> SourceValue:
    value = cell.value
    return SourceValue(
        None if value is None else str(value), f"{cell.parent.title}!{cell.coordinate}"
    )


def label(cell: Cell | MergedCell) -> str:
    if not isinstance(cell.value, str):
        return ""
    first, *_ = cell.value.splitlines() or [""]
    return first.removesuffix("_x000D_").strip()


def parsed_date(field: SourceValue, *, keep_short: bool = False) -> date | None:
    if field.value is None:
        return None
    raw = field.value.strip()
    if keep_short and len(raw) == 6 and raw.isascii() and raw.isdigit():
        return None
    try:
        if len(raw) == 8 and raw.isascii() and raw.isdigit():
            return datetime.strptime(raw, "%Y%m%d").date()
        if len(raw) == 10:
            return date.fromisoformat(raw)
        if len(raw) == 19 and raw.endswith(" 00:00:00"):
            return date.fromisoformat(raw[:10])
    except ValueError as error:
        raise ParseError("invalid delivery date", location=field.cell) from error
    raise ParseError("unsupported delivery date", location=field.cell)


def _verify_archive(path: Path) -> None:
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise ParseError("Expected an XLSX file within the size limit")
    with ZipFile(path) as archive:
        parts = archive.infolist()
        if len(parts) > _MAX_PARTS or sum(item.file_size for item in parts) > _MAX_EXPANDED_BYTES:
            raise ParseError("Workbook exceeds archive limits")


def load_rows(path: Path) -> WorkbookRows:
    try:
        _verify_archive(path)
        with path.open("rb") as source:
            workbook = load_workbook(source, keep_links=False)
    except (BadZipFile, InvalidFileException, KeyError) as error:
        raise ParseError("Invalid XLSX package") from error

    try:
        if len(workbook.worksheets) != 1:
            raise ParseError("Expected one worksheet")
        sheet = workbook.worksheets[0]
        if sheet.max_row > _MAX_ROWS or sheet.max_column > _MAX_COLUMNS:
            raise ParseError("Worksheet exceeds row or column limits")
        rows = list(sheet.iter_rows(max_col=sheet.max_column + 1))
        for row in rows:
            formula = next((cell for cell in row if cell.data_type == "f"), None)
            if formula is not None:
                raise ParseError(
                    "unexpected formula", location=f"{sheet.title}!{formula.coordinate}"
                )
        return rows
    finally:
        workbook.close()


def decimal_value(field: SourceValue) -> Decimal:
    try:
        amount = Decimal(field.value or "")
    except InvalidOperation as error:
        raise ParseError("invalid quantity", location=field.cell) from error
    coefficient = amount.as_tuple()
    exponent = coefficient.exponent
    if (
        not amount.is_finite()
        or len(coefficient.digits) > 65
        or not isinstance(exponent, int)
        or exponent < -60
        or not -60 <= amount.adjusted() <= 60
    ):
        raise ParseError("quantity is out of range", location=field.cell)
    return amount


def labels(row: WorkbookRow) -> dict[str, int]:
    return {name: index for index, cell in enumerate(row) if (name := label(cell))}


def adjacent_fields(
    row: WorkbookRow, column_by_label: dict[str, int], keys: frozenset[str]
) -> dict[str, SourceValue]:
    return {
        key: fixed_value(row[column_by_label[key] + 1])
        for key in keys.intersection(column_by_label)
        if column_by_label[key] + 1 < len(row)
    }
