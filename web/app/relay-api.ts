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
  invalid_login: "用户名或密码不正确，或账号已停用。",
  invalid_user_fields: "请检查用户名格式。新密码须为 8 至 128 个字符。",
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
};

export class RelayRequestError extends Error {
  constructor(public status: number, code: string) {
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
