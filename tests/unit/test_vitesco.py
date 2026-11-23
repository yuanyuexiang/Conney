from decimal import Decimal
from pathlib import Path

import pytest
from openpyxl import Workbook

from relay.parsers.vitesco import ParseError, parse_vitesco


def _fixture(path: Path, quantity: str) -> None:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.title = "Relay"
    sheet.append(["Vitesco-DELFOR-长期交付预测"])
    sheet.append(["DelforNo", "0024", "MessageFunction\n代码", None, "PreviousDeliveryNo", "0023"])
    sheet.append(["SupplierNo_x000D_\n编号", "000001"])
    sheet.append(["LineItemNo", "1", "BuyerItemNo", "000123"])
    sheet.append(["WarehousePlace", "ItemDes"])
    sheet.append(["0100", "Synthetic part"])
    sheet.append(
        [
            "DeliveryPlanStatusIndicator",
            "QtyType",
            "Qty",
            "QtyUnit",
            "RequestedDeliveryDate",
            "EarliestDeliveryDate",
            "LastDeliveryDate",
        ]
    )
    sheet.append(["UNKNOWN", "113", quantity, "C62", None, "20261214", "20261220"])
    workbook.save(path)


def test_preserves_zero_interval_and_source_codes(tmp_path: Path) -> None:
    attachment = tmp_path / "attachment.xlsx"
    _fixture(attachment, "0.00")

    document = parse_vitesco(attachment)
    (block,) = document.blocks
    (record,) = block.quantities
    assert document.fields["PreviousDeliveryNo"].value == "0023"
    assert document.fields["MessageFunction"].value is None
    assert document.fields["SupplierNo"].value == "000001"
    assert block.fields["BuyerItemNo"].value == "000123"
    assert block.fields["WarehousePlace"].value == "0100"
    assert record.quantity.as_tuple() == Decimal("0.00").as_tuple()
    assert record.source["Qty"].cell == "Relay!C8"
    assert record.source["DeliveryPlanStatusIndicator"].value == "UNKNOWN"
    assert record.requested_date is None
    assert record.earliest_date and record.earliest_date.isoformat() == "2026-12-14"
    assert record.latest_date and record.latest_date.isoformat() == "2026-12-20"


def test_rejects_formula_instead_of_treating_cached_value_as_quantity(tmp_path: Path) -> None:
    attachment = tmp_path / "attachment.xlsx"
    _fixture(attachment, "=1+1")
    with pytest.raises(ParseError, match=r"Relay!8: unexpected formula"):
        parse_vitesco(attachment)
