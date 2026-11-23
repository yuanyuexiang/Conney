from pathlib import Path

import pytest

from relay.parsers.schedule_workbooks import parse_workbook

_SAMPLES = Path(__file__).resolve().parents[2] / "samples" / "edi" / "raw"


@pytest.mark.parametrize(
    ("prefix", "template", "blocks", "records", "zeroes"),
    [
        ("Vitesco", "vitesco_delfor", 1, 88, 19),
        ("DRX", "drx_delfor", 13, 309, None),
        ("JLR_DELFOR", "jlr_delfor", 1, 37, None),
        ("JLR_DELJIT", "jlr_deljit", 1, 11, None),
        ("Kroschu", "kroschu_4905", 40, 190, 40),
    ],
)
def test_local_customer_corpus_preserves_original_structure(
    prefix: str, template: str, blocks: int, records: int, zeroes: int | None
) -> None:
    path = next(_SAMPLES.glob(f"{prefix}*.xlsx"), None)
    if path is None:
        pytest.skip("Original customer samples are local only")
    document = parse_workbook(path)
    rows = [row for block in document.blocks for row in block.quantities]
    assert document.template == template
    assert len(document.blocks) == blocks
    assert len(rows) == records
    if zeroes is not None:
        assert sum(row.quantity == 0 for row in rows) == zeroes
    if template == "jlr_deljit":
        assert sum(row.source["RequestedDeliveryDate"].value is None for row in rows) == 2
        assert any(row.source["QtyType"].value == "79" for row in rows)
    if template == "kroschu_4905":
        assert sum(row.source["DateTime"].value == "000000" for row in rows) == 40
        assert all(row.source["QtyUnit"].value == "ST" for row in rows)
        assert document.blocks[0].fields["CustomerItemNo"].value == "01029077"
