import type { DeliveryTemplate } from "./delivery-api";

export type OutgoingHost = "smtp.263.net" | "smtpw.263.net" | "smtp.163.com";

export type OutgoingConfig = {
  address: string;
  host: OutgoingHost;
  domains: string[];
  test_recipient: string | null;
  revision: string;
  verified: boolean;
};

export type RecipientGroup = {
  id: number;
  name: string;
  addresses: string[];
  active: boolean;
  revision: number;
};

export type Release = {
  id: number;
  run_id: number;
  version: number;
  draft_revision: number;
  rule_version: string;
  output_version: string;
  template: DeliveryTemplate;
  ship_through: string | null;
  retained_issues: number;
  document_no: string | null;
  filename: string;
  confirmed_at: string;
  confirmed_by: string;
};

export type ReleaseBlocker = {
  kind: string;
  customer_item: string | null;
  order_no: string | null;
  cell: string | null;
  requested_date: string | null;
  reason: string | null;
};

export type ReleasePreview = {
  pending_mappings: number;
  pending_reviews: number;
  pending_shipments: number;
  future_unscheduled: number;
  scheduled: number;
  blockers: ReleaseBlocker[];
  calculated_quantities: number;
  retained_issues: number;
  retained_details: ReleaseBlocker[];
};

export type SendOptions = {
  sender: string | null;
  mailbox_revision: string | null;
  verified: boolean;
  test_mode: boolean;
  groups: RecipientGroup[];
  attempted_addresses: string[];
};

export type RecipientState = "queued" | "sending" | "accepted" | "failed" | "uncertain";
export type DispatchRecipient = {
  id: number;
  address: string;
  departments: string[];
  message_id: string;
  state: RecipientState;
  detail: string | null;
  smtp_code: number | null;
  finished_at: string | null;
  retryable: boolean;
};

export type DispatchRecord = {
  id: number;
  sender: string;
  subject: string;
  body: string;
  retry_of: number | null;
  created_at: string;
  created_by: string;
  recipients: DispatchRecipient[];
};
