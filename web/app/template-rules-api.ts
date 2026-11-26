export type TemplateAttachment = {
  code: string;
  attachmentId?: number;
  filename?: string;
  parserVersion?: string | null;
  mailId?: number;
};

export type WorkbookTemplate = {
  code: string;
  name: string;
  customer: string;
  recognition: string;
  required_fields: string[];
  rule_notes: string[];
  output_version: string | null;
  output_sheets: string[];
};

export type DeliveryLeadTimes = { air_days: number; sea_days: number };

export type EtaChange = {
  mode: "air" | "sea";
  ship_date: string;
  before: string;
  after: string;
  customer_item: string | null;
  quantity_cell: string | null;
};

export type RuleImpact = {
  attachment_id: number;
  filename: string;
  document_no: string | null;
  parser_version: string;
  sample_revision: number | null;
  material_count: number;
  quantity_count: number;
  pending_entries: number;
  pending_shipments: number;
  changed_eta_count: number;
  changed_etas: EtaChange[];
  simulated_etas: EtaChange[];
};

export type DrxRuleVersion = {
  id: number;
  version: string;
  parameters: DeliveryLeadTimes;
  source_note: string;
  baseline_id: number | null;
  trial: RuleImpact | null;
  created_at: string;
  created_by: string | null;
  published_at: string | null;
  published_by: string | null;
};

export type DrxRules = {
  active: DrxRuleVersion;
  versions: DrxRuleVersion[];
  samples: {
    attachment_id: number;
    filename: string;
    document_no: string | null;
    document_date: string | null;
    parser_version: string;
  }[];
};

export type DraftRulePreview = { target: DrxRuleVersion; impact: RuleImpact; revision: number };
export type DraftRuleHistory = { revision: number; rule_version: string; replaced_at: string };
