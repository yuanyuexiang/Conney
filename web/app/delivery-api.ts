export const customerLabels = {
  drx: "德科 DRX",
  kroschu: "科伯舒特",
  jlr: "捷豹路虎",
  vitesco: "舍弗勒 / Vitesco",
  zf: "采埃孚 ZF",
} as const;

export type Customer = keyof typeof customerLabels;

export type MappingValues = {
  customer: Customer;
  customer_item: string;
  internal_item: string;
  order_no: string | null;
  order_line: string | null;
  ship_to: string | null;
  supplier_item: string | null;
  drawing: string | null;
  minimum_pack: number | null;
  units_per_carton: number | null;
  cartons_per_pallet: number | null;
  safety_stock: number | null;
};

export type MaterialMapping = MappingValues & { id: number; revision: number };

export type MaterialCandidate = Pick<MappingValues,
  "customer" | "customer_item" | "order_no" | "order_line" | "ship_to" | "supplier_item"
> & {
  id: number;
  source_filename: string;
  source_cell: string;
  source_date: string;
};

export type Shipment = {
  ship_date: string;
  mode: "air" | "sea";
  source_note: string;
  manual_eta: string | null;
  entered_by: string;
};

export type Entry = {
  id: number;
  quantity: string;
  raw_quantity: string | null;
  code: string | null;
  unit: string | null;
  status_code: string | null;
  quantity_cell: string | null;
  date_cell: string | null;
  end_date_cell: string | null;
  requested_date: string | null;
  earliest_date: string | null;
  latest_date: string | null;
  period: string;
  state: "scheduled" | "recorded_zero" | "transport_missing" | "period_review" | "code_review";
  shipment: Shipment | null;
  eta: string | null;
};

export type Material = {
  id: number;
  customer_item: string | null;
  customer_item_cell: string | null;
  order_no: string | null;
  order_line: string | null;
  ship_to: string | null;
  supplier_item: string | null;
  mapping_state: "matched" | "missing" | "ambiguous" | "supplier_mismatch";
  mapping: MaterialMapping | null;
  entries: Entry[];
};

export type Summary = {
  id: number;
  attachment_id: number;
  filename: string;
  document_no: string | null;
  document_date: string | null;
  pending_mappings: number;
  pending_entries: number;
};

export type DeliveryDraft = Summary & {
  rule_version: string;
  mail_id: number;
  revision: number;
  materials: Material[];
};

export const mappingLabels: Record<Material["mapping_state"], string> = {
  matched: "已匹配",
  missing: "未匹配",
  ambiguous: "匹配冲突",
  supplier_mismatch: "供应商料号冲突",
};

export const entryLabels: Record<Entry["state"], string> = {
  scheduled: "已安排",
  recorded_zero: "零需求",
  transport_missing: "待补运输",
  period_review: "区间待核对",
  code_review: "代码待核对",
};

export function needsReview(entry: Entry) {
  return entry.state !== "scheduled" && entry.state !== "recorded_zero";
}

export function shortDate(value: string | null) {
  if (!value) return "—";
  return /^\d{8}$/.test(value) ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}` : value;
}

export function addQuantities(entries: Entry[]) {
  const parts = entries.map(({ quantity }) => quantity.split("."));
  const scale = Math.max(0, ...parts.map(([, fraction]) => fraction?.length ?? 0));
  const scaled = parts.reduce((sum, [whole, fraction = ""]) => (
    sum + BigInt(whole + fraction.padEnd(scale, "0"))
  ), BigInt(0));
  const negative = scaled < BigInt(0);
  const digits = (negative ? -scaled : scaled).toString().padStart(scale + 1, "0");
  const whole = scale ? digits.slice(0, -scale) : digits;
  const fraction = scale ? digits.slice(-scale).replace(/0+$/, "") : "";
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}${fraction ? `.${fraction}` : ""}`;
}
