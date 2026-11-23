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
  user_not_found: "该用户不存在，请刷新后重试。",
  use_own_password_change: "请从右上角账号菜单选择「修改密码」。",
  mailbox_admin_unavailable: "邮箱加密密钥未配置，请联系部署管理员。",
  mailbox_storage_unavailable: "邮箱配置暂不可用，请稍后重试。",
  mailbox_invalid_fields: "请检查邮箱地址、文件夹、日期和轮询间隔。",
  mailbox_credential_required: "首次配置或更换邮箱、服务器时，请填写授权码。",
  mailbox_credential_invalid: "已保存的授权码无法读取，请重新填写后保存。",
  mailbox_not_configured: "请先保存邮箱配置。",
  mailbox_config_changed: "邮箱配置已修改，请刷新后重新测试。",
  mailbox_test_required: "请通过连接测试后再启用采集。",
  operations_unavailable: "暂时无法读取运行监测数据。",
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
