export type Role = "admin" | "viewer";

export type User = {
  id: number;
  username: string;
  role: Role;
  active: boolean;
  must_change_password: boolean;
};

export const roleLabels: Record<Role, string> = { admin: "管理员", viewer: "查询用户" };

const messages: Record<string, string> = {
  drx_operation_only: "此操作适用于德科 DRX 草稿。请使用当前模板的需求明细及导出。",
  draft_has_confirmed_release: "该批次已有确认版，需要保留草稿及版本记录。",
  release_already_confirmed: "当前草稿版本已确认，请查看确认版本。",
  release_has_blockers: "本次交付仍有待解决事项，请重新核验。",
  release_cutoff_required: "请选择核验交货截至日。",
  release_unexpected_cutoff: "当前模板按整批核验，请重新打开确认交付。",
  release_retained_ack_required: "请确认已核对确认版中保留的原值和区间。",
  release_dispatch_in_progress: "上一确认版还有待发送邮件，请等待发送结束。",
  release_superseded: "此版本已有更新的确认版，请重新打开确认版本。",
  release_not_found: "没有找到确认版。",
  release_workbook_too_large: "交付文件超过 10 MB，无法作为邮件确认版保存。",
  release_workbook_damaged: "归档附件校验失败，请检查确认版文件。",
  dispatch_invalid_fields: "请检查邮箱地址、发件服务器、内域或邮件内容。",
  dispatch_address_not_allowed: "收件地址超出当前发件设置允许的范围。",
  dispatch_group_exists: "已存在同名部门收件组。",
  dispatch_groups_changed: "可用部门收件组已变化，请刷新后核对地址。",
  dispatch_recipients_changed: "待发送的实际地址已变化，请刷新后核对。",
  dispatch_no_recipients: "没有可发送的地址，请查看发送记录。",
  dispatch_request_conflict: "发送请求已存在，请查看发送记录后重试。",
  dispatch_not_found: "没有找到该发送记录。",
  dispatch_storage_unavailable: "发件配置读取失败，本次邮件尚未提交。",
  outgoing_not_configured: "请先保存发件配置。",
  outgoing_credential_required: "新增或更换发件账号时，请填写授权码。",
  outgoing_not_verified: "请在邮箱配置完成 SMTP 登录测试。",
  outgoing_changed: "发件配置已变化，请核对设置后重新提交。",
  outgoing_sender_changed: "当前发件邮箱与原邮件不同，无法直接补发。",
  smtp_auth_failed: "SMTP 登录失败，请核对发件账号、客户端授权码和权限。",
  smtp_tls_failed: "SMTP 的 TLS 证书验证失败。",
  smtp_unreachable: "无法连接发件服务器。",
  smtp_sender_rejected: "邮件服务器拒绝了发件地址。",
  smtp_recipient_rejected: "邮件服务器拒绝了该收件地址。",
  smtp_message_rejected: "邮件服务器拒绝了邮件正文或附件。",
  smtp_acceptance_unknown: "提交邮件时连接中断，请在邮箱侧核实。",
  smtp_worker_interrupted: "发件过程被中断，请在邮箱侧核实。",
  invalid_login: "用户名或密码不正确，或账号已停用。",
  invalid_user_fields: "请检查用户名格式。新密码须为 8 至 128 个字符。",
  invalid_rule_fields: "请填写试算附件、发货日、运输天数和业务依据。运输天数须为 0 至 365 的整数。",
  login_required: "登录已过期，请重新登录。",
  admin_required: "此操作需要管理员权限。",
  password_change_required: "请先修改临时密码。",
  current_password_invalid: "当前密码不正确。",
  password_unchanged: "请设置与当前密码不同的新密码。",
  username_exists: "此用户名已存在。",
  cannot_change_own_access: "请使用其他管理员账号修改此账号的权限。",
  cannot_delete_own_account: "当前登录账号不能删除。",
  permission_denied: "管理员权限已变更，请重新登录。",
  user_not_found: "该用户不存在，请刷新后重试。",
  use_own_password_change: "请从右上角账号菜单选择「修改密码」。",
  mailbox_admin_unavailable: "邮箱加密密钥未配置，请联系部署管理员。",
  mailbox_storage_unavailable: "邮箱配置暂不可用，请稍后重试。",
  mailbox_invalid_fields: "请检查邮箱地址与 IMAP 服务器是否匹配，以及文件夹、日期和轮询间隔。",
  mailbox_credential_required: "首次配置或更换邮箱、服务器时，请填写授权码。",
  mailbox_credential_invalid: "已保存的授权码无法读取，请重新填写后保存。",
  mailbox_not_configured: "请先保存邮箱配置。",
  mailbox_config_changed: "邮箱配置已修改，请刷新后重新测试。",
  mailbox_test_required: "请通过连接测试后再启用采集。",
  operations_unavailable: "暂时无法读取运行监测数据。",
  dashboard_unavailable: "暂时无法读取仪表盘数据，请刷新后重试。",
  invalid_delivery_fields: "请检查料号、订单、数量或发货日期。人工 ETA 须晚于或等于发货日。",
  invalid_request_origin: "请求已失效，请刷新页面后重试。",
  mapping_scope_exists: "同一客户、料号和匹配范围已有映射，请编辑已有记录。",
  mapping_changed: "此映射已被更新，请关闭对话框并刷新列表后重试。",
  draft_not_found: "该交付草稿不存在。",
  draft_changed: "草稿已更新，请核对当前版本后重试。",
  delivery_source_unavailable: "此附件尚未完成德科报文解析。",
  delivery_entry_not_found: "该数量记录不存在，请重新打开草稿。",
  delivery_entry_requires_review: "请先确认物料匹配、数量代码和交货日期。",
  delivery_eta_out_of_range: "计算的到货日超出日期范围，请检查发货日和运输天数。",
  rule_baseline_changed: "生效的规则版本已变更，请用当前版本重新试算。",
  rule_sample_changed: "样本草稿在试算后已更新。请用最新草稿重新试算后发布。",
  rule_parameters_unchanged: "运输天数与生效版本相同。修改参数后即可试算。",
  rule_version_not_found: "该规则版本已不可用，请刷新列表。",
  delivery_review_unavailable: "当前记录无法核对。已有发货安排时，请先清除该安排。",
  invalid_delivery_review: "请检查核对选项和依据。确认的交货日须在原报文区间内。",
  invalid_delivery_review_group: "批量确认只适用于类型、状态和单位一致的待确认记录，请更新列表后重试。",
};

export class RelayRequestError extends Error {
  constructor(public status: number, public code: string) {
    super(status === 429 ? "登录尝试过于频繁，请稍后重试。" : messages[code] ?? "操作未成功，请稍后重试。");
  }
}

export async function relayRequest<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Relay-Request", "1");
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`/api/v1${path}`, { ...init, headers, cache: "no-store" });

  if (!response.ok) {
    const payload: { detail?: string } = await response.json().catch(() => ({}));
    if (response.status === 401 && path !== "/auth/login" && path !== "/auth/me") {
      window.dispatchEvent(new Event("relay-session-expired"));
    }
    throw new RelayRequestError(response.status, payload.detail ?? "");
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export function messageOf(error: unknown): string {
  return error instanceof RelayRequestError ? error.message : "无法连接 Relay，请稍后重试。";
}
