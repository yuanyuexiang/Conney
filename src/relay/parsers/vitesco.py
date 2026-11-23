from pathlib import Path

from relay.parsers.document import (
    MaterialBlock,
    ParsedDocument,
    QuantityRecord,
    SourceValue,
    WorkbookRow,
    WorkbookRows,
    adjacent_fields,
    decimal_value,
    fixed_value,
    label,
    load_rows,
    parsed_date,
)
from relay.parsers.document import ParseError as ParseError

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


def _quantity(row: WorkbookRow, columns: dict[str, int]) -> QuantityRecord:
    source = {field: fixed_value(row[index]) for field, index in columns.items()}
    raw = source["Qty"]
    amount = decimal_value(raw)
    start = parsed_date(source["EarliestDeliveryDate"])
    end = parsed_date(source["LastDeliveryDate"])
    if (start is None) != (end is None) or (start and end and start > end):
        raise ParseError(f"{source['EarliestDeliveryDate'].cell}: invalid delivery interval")
    return QuantityRecord(amount, parsed_date(source["RequestedDeliveryDate"]), start, end, source)


def parse_vitesco(path: Path) -> ParsedDocument:
    return parse_vitesco_rows(load_rows(path))


def parse_vitesco_rows(rows: WorkbookRows) -> ParsedDocument:
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
        labels = {name: index for index, cell in enumerate(row[:-1]) if (name := label(cell))}
        if {"LineItemNo", "BuyerItemNo"}.issubset(labels):
            if block_fields:
                blocks.append(_finish_block(block_fields, records))
            block_fields = adjacent_fields(row, labels, _BLOCK_FIELDS)
            columns = {}
            records = []
        elif block_fields and not columns and "WarehousePlace" in labels:
            if row_number >= len(rows):
                raise ParseError("Missing material attributes beneath the block header")
            attributes = rows[row_number]
            block_fields.update(
                {
                    key: fixed_value(attributes[labels[key]])
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
            fields.update(adjacent_fields(row, labels, _HEADER_FIELDS))

    if block_fields:
        blocks.append(_finish_block(block_fields, records))
    if not blocks or not fields.get("DelforNo") or not fields["DelforNo"].value:
        raise ParseError("Missing document number or material block")
    return ParsedDocument(fields, tuple(blocks), "vitesco_delfor")


def _finish_block(fields: dict[str, SourceValue], records: list[QuantityRecord]) -> MaterialBlock:
    if not fields["BuyerItemNo"].value or not records:
        raise ParseError(f"{fields['BuyerItemNo'].cell}: missing material number or quantity rows")
    return MaterialBlock(fields, tuple(records))
