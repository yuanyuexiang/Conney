export type MailStatus = "complete" | "review" | "queued" | "failed";

export const statusLabels: Record<MailStatus, string> = {
  complete: "解析完成",
  review: "待核对",
  queued: "待处理",
  failed: "解析失败",
};

export type PlanRow = {
  material: string;
  period: string;
  quantity: string;
  unit: string;
  source: string;
};

export type MailRecord = {
  id: string;
  template: string;
  sender: string;
  address: string;
  subject: string;
  received: string;
  filename: string;
  size: string;
  status: MailStatus;
  note?: string;
  rows: PlanRow[];
};

export const demoMail: MailRecord[] = [
  {
    id: "vitesco",
    template: "Vitesco DELFOR",
    sender: "Vitesco 计划组",
    address: "planning@example.com",
    subject: "Vitesco 交付预测 · 第 48 周",
    received: "11-23 09:42",
    filename: "DEMO_Vitesco_DELFOR.xlsx",
    size: "5 KB",
    status: "complete",
    rows: [
      { material: "VT-10024", period: "2026-11-30 至 12-06", quantity: "1,200", unit: "C62", source: "Sheet1!A2:E2" },
      { material: "VT-10024", period: "2026-12-07 至 12-13", quantity: "960", unit: "C62", source: "Sheet1!A3:E3" },
      { material: "VT-10024", period: "2026-12-14 至 12-20", quantity: "0", unit: "C62", source: "Sheet1!A4:E4" },
    ],
  },
  {
    id: "drx",
    template: "DRX DELFOR",
    sender: "德科交付计划",
    address: "schedule@example.com",
    subject: "DRX 滚动交付计划 · 11 月",
    received: "11-23 09:18",
    filename: "DEMO_DRX_DELFOR.xlsx",
    size: "5 KB",
    status: "review",
    note: "部分原始数量类型的业务含义待确认。",
    rows: [
      { material: "DR-20018", period: "2026-12-01", quantity: "480", unit: "C62", source: "Sheet1!A2:E2" },
      { material: "DR-20034", period: "2026-12-04", quantity: "240", unit: "C62", source: "Sheet1!A3:E3" },
    ],
  },
  {
    id: "deljit",
    template: "JLR DELJIT",
    sender: "JLR 交付调度",
    address: "dispatch@example.com",
    subject: "JLR 精确交付计划 · DELJIT",
    received: "11-23 08:56",
    filename: "DEMO_JLR_DELJIT.xlsx",
    size: "5 KB",
    status: "complete",
    rows: [
      { material: "JL-34012", period: "2026-11-25", quantity: "720", unit: "EA", source: "Sheet1!A2:E2" },
    ],
  },
  {
    id: "kroschu",
    template: "Kroschu 4905",
    sender: "Kroschu 供应链",
    address: "supply@example.com",
    subject: "Kroschu 4905 · 交付预测",
    received: "11-22 16:35",
    filename: "DEMO_Kroschu_4905.xlsx",
    size: "5 KB",
    status: "failed",
    note: "样例缺少数量列，请核对模板表头。",
    rows: [],
  },
  {
    id: "delfor",
    template: "JLR DELFOR",
    sender: "JLR 需求计划",
    address: "forecast@example.com",
    subject: "JLR 需求预测 · DELFOR",
    received: "11-22 14:08",
    filename: "DEMO_JLR_DELFOR.xlsx",
    size: "5 KB",
    status: "queued",
    rows: [],
  },
  {
    id: "vitesco-old",
    template: "Vitesco DELFOR",
    sender: "Vitesco 计划组",
    address: "planning@example.com",
    subject: "Vitesco 交付预测 · 第 47 周",
    received: "11-20 10:12",
    filename: "DEMO_Vitesco_DELFOR_W47.xlsx",
    size: "5 KB",
    status: "complete",
    rows: [
      { material: "VT-10024", period: "2026-11-23 至 11-29", quantity: "840", unit: "C62", source: "Sheet1!A2:E2" },
    ],
  },
];
