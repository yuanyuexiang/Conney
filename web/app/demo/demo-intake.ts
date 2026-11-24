import type { Detail, IntakeSource, MailState, Quantity } from "../intake-workspace";
import type { IntakeCounts } from "../workbench-status";

type SampleRow = readonly [material: string, date: string | readonly [string, string], quantity: string, unit: string];
type Sample = {
  id: number;
  sender: string;
  subject: string;
  received: string;
  file: string;
  bytes: number;
  template: string;
  state: MailState;
  rows: readonly SampleRow[];
};

const samples: Sample[] = [
  {
    id: 6, sender: "Vitesco 计划组 <planning@example.com>", subject: "Vitesco 交付预测 · 第 48 周",
    received: "2026-11-23T09:42:00+08:00", file: "DEMO_Vitesco_DELFOR.xlsx",
    bytes: 5076, template: "vitesco_delfor", state: "complete",
    rows: [
      ["VT-10024", ["2026-11-30", "2026-12-06"], "1200", "C62"],
      ["VT-10024", ["2026-12-07", "2026-12-13"], "960", "C62"],
      ["VT-10024", ["2026-12-14", "2026-12-20"], "0", "C62"],
    ],
  },
  {
    id: 5, sender: "德科交付计划 <schedule@example.com>", subject: "DRX 滚动交付计划 · 11 月",
    received: "2026-11-23T09:18:00+08:00", file: "DEMO_DRX_DELFOR.xlsx",
    bytes: 5027, template: "drx_delfor", state: "review",
    rows: [["DR-20018", "2026-12-01", "480", "C62"], ["DR-20034", "2026-12-04", "240", "C62"]],
  },
  {
    id: 4, sender: "JLR 交付调度 <dispatch@example.com>", subject: "JLR 精确交付计划 · DELJIT",
    received: "2026-11-23T08:56:00+08:00", file: "DEMO_JLR_DELJIT.xlsx",
    bytes: 4993, template: "jlr_deljit", state: "complete",
    rows: [["JL-34012", "2026-11-25", "720", "EA"]],
  },
  {
    id: 3, sender: "Kroschu 供应链 <supply@example.com>", subject: "Kroschu 4905 · 交付预测",
    received: "2026-11-22T16:35:00+08:00", file: "DEMO_Kroschu_4905.xlsx",
    bytes: 4962, template: "kroschu_4905", state: "failed", rows: [],
  },
  {
    id: 2, sender: "JLR 需求计划 <forecast@example.com>", subject: "JLR 需求预测 · DELFOR",
    received: "2026-11-22T14:08:00+08:00", file: "DEMO_JLR_DELFOR.xlsx",
    bytes: 4928, template: "jlr_delfor", state: "queued", rows: [],
  },
  {
    id: 1, sender: "Vitesco 计划组 <planning@example.com>", subject: "Vitesco 交付预测 · 第 47 周",
    received: "2026-11-20T10:12:00+08:00", file: "DEMO_Vitesco_DELFOR_W47.xlsx",
    bytes: 5004, template: "vitesco_delfor", state: "complete",
    rows: [["VT-10024", ["2026-11-23", "2026-11-29"], "840", "C62"]],
  },
];

function asDetail(sample: Sample): Detail {
  const { id, state, sender, subject, received, file, bytes, template } = sample;
  const parsed = state === "complete" || state === "review";
  const failed = state === "failed";
  return {
    mail: {
      id, sender, subject, state, received_at: received, attachment_count: 1,
      intake_error_code: null, templates: parsed ? [template] : [],
    },
    attachments: [{
      id, filename: file, size_bytes: bytes, intake_state: "archived",
      job_state: parsed ? "succeeded" : failed ? "failed" : "pending",
      error_code: failed ? "invalid_or_unsupported_workbook" : null,
      error_reason: failed ? "missing quantity column" : null,
      error_location: failed ? "Sheet1!C1" : null,
      review_location: state === "review" ? "Sheet1!C2" : null,
      review_reason: state === "review" ? "quantity" : undefined,
      template: parsed ? template : null,
    }],
  };
}

function asQuantity([material, date, quantity, unit]: SampleRow, index: number): Quantity {
  const [start, end] = typeof date === "string" ? [null, null] : date;
  const requested = typeof date === "string" ? date : null;
  return {
    id: index + 1, material, quantity, unit, quantity_type: null, requested_date: requested,
    earliest_date: start, latest_date: end, quantity_cell: `Sheet1!C${index + 2}`,
    raw_delivery_date: requested, delivery_date_cell: `Sheet1!B${index + 2}`,
    raw_interval_start: null, raw_interval_end: null,
  };
}

const details = samples.map(asDetail);
const actionable = new Set<MailState>(["failed", "review", "queued"]);

export const demoCounts: IntakeCounts = {
  total: samples.length,
  failed: samples.filter(({ state }) => state === "failed").length,
  review: samples.filter(({ state }) => state === "review").length,
  queued: samples.filter(({ state }) => state === "queued").length,
};

export const demoIntake: IntakeSource = {
  async list({ state, query, before, limit }) {
    const term = query.trim().toLowerCase();
    return details.filter(({ mail, attachments }) =>
      (before === undefined || mail.id < before)
      && (state === "all" || (state === "actionable" ? actionable.has(mail.state) : mail.state === state))
      && [mail.subject, mail.sender, ...attachments.map(({ filename }) => filename)]
        .some((value) => value.toLowerCase().includes(term)),
    ).slice(0, limit).map(({ mail }) => mail);
  },
  async detail(id) {
    const detail = details.find(({ mail }) => mail.id === id);
    if (!detail) throw new Error("Demo mail not found");
    return detail;
  },
  async quantities(id, after, limit) {
    return (samples.find((sample) => sample.id === id)?.rows ?? [])
      .map(asQuantity).filter((row) => row.id > after).slice(0, limit);
  },
  fileUrl: ({ filename }) => `/demo/${encodeURIComponent(filename)}`,
};
