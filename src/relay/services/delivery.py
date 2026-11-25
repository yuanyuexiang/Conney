from collections import defaultdict
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_serializer,
    model_validator,
)

Customer = Literal["drx", "kroschu", "jlr", "vitesco", "zf"]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
PackSize = Annotated[int, Field(gt=0, le=1_000_000_000)]
MappingState = Literal["matched", "missing", "ambiguous", "supplier_mismatch"]
EntryState = Literal[
    "scheduled", "recorded_zero", "transport_missing", "period_review", "code_review"
]

RULE_VERSION = "drx-1"


def matching_order(customer: Customer, order: str | None) -> str | None:
    if customer == "drx" and order and order.isascii() and order.isdecimal():
        return order.lstrip("0") or "0"
    return order


class MaterialScope(BaseModel):
    customer: Customer
    customer_item: Identifier
    order_no: Identifier | None = None
    order_line: Identifier | None = None
    ship_to: Identifier | None = None
    supplier_item: Identifier | None = None

    @model_validator(mode="after")
    def check_scope(self) -> "MaterialScope":
        if self.customer == "drx" and self.order_no is None:
            raise ValueError("A DRX mapping requires a purchase order")
        return self


class MappingValues(MaterialScope):
    model_config = ConfigDict(extra="forbid")

    internal_item: Identifier
    drawing: Identifier | None = None
    minimum_pack: PackSize | None = None
    units_per_carton: PackSize | None = None
    cartons_per_pallet: PackSize | None = None
    safety_stock: Annotated[int, Field(ge=0, le=1_000_000_000)] | None = None


class MaterialObservation(MaterialScope):
    source_filename: str
    source_cell: str
    source_date: date


class MaterialCandidate(MaterialObservation):
    id: int


class MaterialMapping(MappingValues):
    id: int
    revision: int


class MappingEdit(MappingValues):
    revision: Annotated[int, Field(ge=1)]


class ShipmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ship_date: Annotated[date, Field(ge=date(1900, 1, 1), le=date(9999, 9, 30))]
    mode: Literal["air", "sea"]
    source_note: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
    ]
    manual_eta: Annotated[date, Field(ge=date(1900, 1, 1))] | None = None

    @model_validator(mode="after")
    def check_arrival(self) -> "ShipmentInput":
        if self.manual_eta is not None and self.manual_eta < self.ship_date:
            raise ValueError("ETA is earlier than shipment")
        return self

    def arrival(self) -> date:
        return self.manual_eta or self.ship_date + timedelta(days=14 if self.mode == "air" else 84)


class Shipment(ShipmentInput):
    entered_by: str


class DraftEntry(BaseModel):
    id: int
    quantity: Decimal
    raw_quantity: str | None = None
    code: str | None
    unit: str | None
    status_code: str | None
    quantity_cell: str | None
    date_cell: str | None
    end_date_cell: str | None = None
    requested_date: date | None
    earliest_date: date | None
    latest_date: date | None
    period: str
    state: EntryState
    shipment: Shipment | None = None
    eta: date | None = None

    @field_serializer("quantity")
    def serialize_quantity(self, quantity: Decimal) -> str:
        return format(quantity, "f")


class DraftMaterial(BaseModel):
    id: int
    customer_item: str | None
    customer_item_cell: str | None
    order_no: str | None
    order_line: str | None
    ship_to: str | None
    supplier_item: str | None
    mapping_state: MappingState
    mapping: MaterialMapping | None
    entries: list[DraftEntry]


class DraftSnapshot(BaseModel):
    rule_version: str
    attachment_id: int
    mail_id: int
    filename: str
    document_no: str | None
    document_date: str | None
    materials: list[DraftMaterial]
    pending_mappings: int
    pending_entries: int


def find_drx_mapping(
    mappings: Iterable[MaterialMapping],
    *,
    item: str | None,
    order: str | None,
    order_line: str | None,
    ship_to: str | None,
    supplier: str | None,
) -> tuple[MappingState, MaterialMapping | None]:
    if not item or not order:
        return "missing", None
    key = matching_order("drx", order)
    candidates: dict[int, list[MaterialMapping]] = defaultdict(list)
    for mapping in mappings:
        if (
            mapping.customer != "drx"
            or mapping.customer_item != item
            or matching_order("drx", mapping.order_no) != key
            or (mapping.order_line is not None and mapping.order_line != order_line)
            or (mapping.ship_to is not None and mapping.ship_to != ship_to)
        ):
            continue
        specificity = int(mapping.order_line is not None) + int(mapping.ship_to is not None)
        candidates[specificity].append(mapping)
    if not candidates:
        return "missing", None
    best = candidates[max(candidates)]
    if len(best) > 1:
        return "ambiguous", None
    chosen = best[0]
    if chosen.supplier_item is not None and chosen.supplier_item != supplier:
        return "supplier_mismatch", chosen
    return "matched", chosen


def demand_period(requested: date | None, earliest: date | None, latest: date | None) -> str:
    if requested is not None:
        return (requested - timedelta(days=requested.weekday())).isoformat()
    if earliest is not None or latest is not None:
        left = earliest.isoformat() if earliest else "?"
        right = latest.isoformat() if latest else "?"
        return f"{left} ~ {right}"
    return "未给日期"


def entry_state(
    code: str | None,
    unit: str | None,
    status_code: str | None,
    requested: date | None,
    quantity: Decimal,
) -> EntryState:
    if code != "1" or unit != "C62" or status_code not in {"1", "4"}:
        return "code_review"
    if requested is None:
        return "period_review"
    return "recorded_zero" if quantity == 0 else "transport_missing"
