export type MailboxStatus = {
  enabled: boolean;
  verified: boolean;
  last_sync_at: string | null;
  last_sync_result: string | null;
  last_sync_count: number | null;
};

export type IntakeCounts = {
  total: number;
  failed: number;
  review: number;
  queued: number;
};

export const chinaTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "short", hour12: false,
});
const syncTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "numeric", day: "numeric",
  hour: "2-digit", minute: "2-digit", hourCycle: "h23",
});

export function formatSyncTime(value: string): string {
  return syncTime.format(new Date(value));
}

export function describeSync(mailbox: MailboxStatus | null | undefined, unavailable: boolean) {
  if (unavailable) return { label: "同步状态读取失败", tone: "unavailable", kind: "unknown" } as const;
  if (mailbox === undefined) return { label: "正在检查邮箱", tone: "checking", kind: "unknown" } as const;
  if (mailbox === null) return { label: "邮箱未接入", tone: "checking", kind: "setup" } as const;
  if (mailbox.last_sync_result === "uidvalidity_changed") {
    return { label: "文件夹变化 · 同步暂停", tone: "unavailable", kind: "folder" } as const;
  }
  if (!mailbox.verified) return { label: "邮箱待测试", tone: "attention", kind: "test" } as const;
  if (!mailbox.enabled) return { label: "采集已停用", tone: "attention", kind: "stopped" } as const;
  if (mailbox.last_sync_result === "ok") {
    return { label: "上次同步成功", tone: "ready", kind: "synced" } as const;
  }
  if (mailbox.last_sync_result) return { label: "上次同步失败", tone: "unavailable", kind: "failed" } as const;
  return { label: "等待首次同步", tone: "checking", kind: "waiting" } as const;
}

export type SyncView = ReturnType<typeof describeSync>;
