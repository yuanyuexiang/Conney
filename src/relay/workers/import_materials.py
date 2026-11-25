import argparse
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import text

from relay.database.session import get_engine
from relay.parsers.document import (
    ParseError,
    SourceValue,
    adjacent_fields,
    load_rows,
    parsed_date,
)
from relay.parsers.schedule_workbooks import parse_workbook
from relay.services.delivery import Customer, MaterialObservation, matching_order


@dataclass(frozen=True)
class MaterialFields:
    customer: Customer
    document_date: str
    item: str
    order: str
    line: str
    ship_to: str
    supplier: str


_FIELDS = {
    "drx_delfor": MaterialFields(
        "drx",
        "DelforCreateDate",
        "BuyerItemNo",
        "poNo",
        "POLineNo",
        "ShipToNo",
        "SupplierItemNumber",
    ),
    "jlr_delfor": MaterialFields(
        "jlr",
        "DelforCreateDate",
        "BuyerItemNo",
        "poNo",
        "POLineNo",
        "ShipToNo",
        "SupplierItemNumber",
    ),
    "jlr_deljit": MaterialFields(
        "jlr",
        "DeljitCreateDate",
        "BuyerItemNo",
        "poNo",
        "POLineNo",
        "ShipToNo",
        "SupplierItemNumber",
    ),
    "kroschu_4905": MaterialFields(
        "kroschu",
        "TransferDate",
        "CustomerItemNo",
        "OrderNo",
        "OrderLineNo",
        "CustomerPlant",
        "SupplierItemNo",
    ),
    "vitesco_delfor": MaterialFields(
        "vitesco",
        "DelforCreateDate",
        "BuyerItemNo",
        "BlanketOrderNo",
        "POLineNo",
        "ShipToNo",
        "SellerItemNo",
    ),
}
_ZF = MaterialFields(
    "zf", "DocDate", "CustomerPartNo", "OrderNo", "OrderLineNo", "ShipToNo", "SupplierPartNo"
)


def _value(fields: dict[str, SourceValue], key: str) -> str | None:
    field = fields.get(key)
    return field.value.strip() or None if field and field.value is not None else None


def _document_date(header: dict[str, SourceValue], key: str) -> date:
    field = header.get(key)
    if field and field.value and len(field.value) == 6:
        try:
            return datetime.strptime(field.value, "%y%m%d").date()
        except ValueError as error:
            raise ParseError("Invalid document date", location=field.cell) from error
    if field and (value := parsed_date(field)) is not None:
        return value
    raise ParseError("Missing document date", location=field.cell if field else None)


def _observation(
    path: Path,
    schema: MaterialFields,
    header: dict[str, SourceValue],
    block: dict[str, SourceValue],
) -> MaterialObservation:
    item = _value(block, schema.item)
    if item is None:
        field = block.get(schema.item)
        raise ParseError("Missing material number", location=field.cell if field else None)
    return MaterialObservation(
        customer=schema.customer,
        customer_item=item,
        order_no=_value(block, schema.order) or _value(header, schema.order),
        order_line=_value(block, schema.line),
        ship_to=_value(block, schema.ship_to) or _value(header, schema.ship_to),
        supplier_item=_value(block, schema.supplier),
        source_filename=path.name,
        source_cell=block[schema.item].cell,
        source_date=_document_date(header, schema.document_date),
    )


def _zf_observations(path: Path) -> Iterator[MaterialObservation]:
    rows = load_rows(path, sheet_name="交付明细")
    if not any(cell.value == "ZF DELFOR 交付预测" for row in rows[:3] for cell in row):
        raise ParseError("Unrecognized ZF workbook title")

    keys = frozenset({_ZF.document_date, _ZF.item, _ZF.order, _ZF.line, _ZF.ship_to, _ZF.supplier})
    header: dict[str, SourceValue] = {}
    block: dict[str, SourceValue] = {}
    for row in rows:
        columns = {
            cell.value.splitlines()[-1].strip(): index
            for index, cell in enumerate(row)
            if isinstance(cell.value, str) and cell.value.strip()
        }
        fields = adjacent_fields(row, columns, keys)
        if _ZF.item in fields:
            if block:
                yield _observation(path, _ZF, header, block)
            block = fields
        elif block:
            block.update(fields)
        else:
            header.update(fields)
    if not block:
        raise ParseError("Missing ZF material block")
    yield _observation(path, _ZF, header, block)


def read_material_observations(path: Path) -> Iterator[MaterialObservation]:
    try:
        document = parse_workbook(path)
    except ParseError as error:
        if error.reason != "Expected one worksheet":
            raise
        yield from _zf_observations(path)
    else:
        schema = _FIELDS[document.template]
        for block in document.blocks:
            yield _observation(path, schema, document.fields, block.fields)


_UPSERT = text("""
    INSERT INTO material_candidates (
        customer, customer_item, order_no, order_key, order_line, ship_to, supplier_item,
        source_filename, source_cell, source_date
    ) VALUES (
        :customer, :customer_item, :order_no, :order_key, :order_line, :ship_to, :supplier_item,
        :source_filename, :source_cell, :source_date
    )
    ON CONFLICT ON CONSTRAINT uq_material_candidate_scope DO UPDATE SET
        order_no = excluded.order_no, source_filename = excluded.source_filename,
        source_cell = excluded.source_cell, source_date = excluded.source_date
    WHERE (excluded.source_date, excluded.source_filename, excluded.source_cell)
        > (material_candidates.source_date, material_candidates.source_filename,
           material_candidates.source_cell)
""")


def import_directory(directory: Path) -> tuple[int, Counter[Customer]]:
    paths = sorted(path for path in directory.rglob("*.xlsx") if not path.name.startswith("~$"))
    if not paths:
        raise ValueError("No XLSX files in the directory")
    unique: dict[tuple[str | None, ...], MaterialObservation] = {}
    for path in paths:
        try:
            for item in read_material_observations(path):
                key = (
                    item.customer,
                    item.customer_item,
                    matching_order(item.customer, item.order_no),
                    item.order_line,
                    item.ship_to,
                    item.supplier_item,
                )
                previous = unique.get(key)
                if previous is None or (
                    item.source_date,
                    item.source_filename,
                    item.source_cell,
                ) > (previous.source_date, previous.source_filename, previous.source_cell):
                    unique[key] = item
        except ValueError as error:
            raise ValueError(f"{path.name}: {error}") from error
    rows = [item.model_dump() | {"order_key": key[2]} for key, item in unique.items()]
    if rows:
        with get_engine().begin() as connection:
            connection.execute(_UPSERT, rows)
    return len(paths), Counter(item.customer for item in unique.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Import pending material candidates from XLSX")
    parser.add_argument("directory", type=Path)
    directory = parser.parse_args().directory
    try:
        files, counts = import_directory(directory)
    except ValueError as error:
        parser.exit(1, f"{error}\n")
    print(f"{files} XLSX files, {sum(counts.values())} unique material candidates")
    for customer, count in sorted(counts.items()):
        print(f"{customer}: {count}")


if __name__ == "__main__":
    main()
