"use client";

import { CheckCircleOutlined, HistoryOutlined, MailOutlined, ReloadOutlined } from "@ant-design/icons";
import { type FormEvent, useEffect, useState } from "react";
import { messageOf, relayRequest } from "./relay-api";
import { WorkbenchSelect } from "./workbench-select";

type Mailbox = {
  address: string;
  host: "imap.263.net" | "imapw.263.net" | "imap.163.com";
  folder: string;
  since: string;
  poll_minutes: number;
  has_password: boolean;
  enabled: boolean;
  verified: boolean;
  last_sync_at: string | null;
  last_sync_result: string | null;
  last_sync_count: number | null;
};

export type CollectionAccount = Pick<Mailbox, "address" | "host" | "has_password">;

const initialMailbox: Mailbox = {
  address: "",
  host: "imap.263.net",
  folder: "INBOX",
  since: "",
  poll_minutes: 5,
  has_password: false,
  enabled: false,
  verified: false,
  last_sync_at: null,
  last_sync_result: null,
  last_sync_count: null,
};

const imapServers: { value: Mailbox["host"]; label: string }[] = [
  { value: "imap.263.net", label: "263 企业邮箱 · imap.263.net" },
  { value: "imapw.263.net", label: "263 企业邮箱 · imapw.263.net" },
  { value: "imap.163.com", label: "网易 163 · imap.163.com" },
];

const connectionMessages: Record<string, string> = {
  ok: "连接成功，已只读打开指定文件夹。",
  auth_failed: "邮箱服务商未接受此账号或授权码，请检查邮箱地址和 IMAP 授权。",
  client_id_failed: "网易 163 未接受客户端标识，请稍后重试。",
  folder_unavailable: "账号已认证，但无法只读打开指定文件夹。",
  tls_failed: "邮件服务器的 TLS 连接验证失败。",
  unreachable: "无法连接邮件服务器，请检查网络或稍后重试。",
};

const syncMessages: Record<string, string> = {
  ok: "同步成功",
  sync_failed: "同步失败，下次将从已归档位置续跑。",
  uidvalidity_changed: "文件夹标识发生变化，同步已暂停等待核对。",
};

const chinaTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "short", hour12: false,
});

export function MailboxSettings({ onChanged, onAccount }: {
  onChanged: () => void;
  onAccount: (value: CollectionAccount | null) => void;
}) {
  const [mailbox, setMailbox] = useState<Mailbox>(initialMailbox);
  const [password, setPassword] = useState("");
  const [dirty, setDirty] = useState(false);
  const [pending, setPending] = useState<"load" | "save" | "test" | "sync" | null>("load");
  const [loaded, setLoaded] = useState(false);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [savedAccount, setSavedAccount] = useState<Pick<Mailbox, "address" | "host"> | null>(null);
  const canKeepPassword = mailbox.has_password && mailbox.address.trim() === savedAccount?.address &&
    mailbox.host === savedAccount.host;
  const addressIs163 = /@163\.com$/i.test(mailbox.address.trim());
  const providerMismatch = mailbox.address.includes("@") && addressIs163 !== (mailbox.host === "imap.163.com");

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<Mailbox | null>("/mailbox", { signal: controller.signal })
      .then((saved) => {
        if (controller.signal.aborted) return;
        setMailbox(saved ?? initialMailbox);
        setSavedAccount(saved);
        onAccount(saved);
        setLoaded(true);
      })
      .catch((error) => {
        if (!controller.signal.aborted) setMessage({ ok: false, text: messageOf(error) });
      })
      .finally(() => { if (!controller.signal.aborted) setPending(null); });
    return () => controller.abort();
  }, [onAccount]);

  useEffect(() => {
    if (!mailbox.enabled || dirty || pending) return;
    const controller = new AbortController();
    const interval = setInterval(() => {
      relayRequest<Mailbox | null>("/mailbox", { signal: controller.signal })
        .then((saved) => {
          if (saved) setMailbox((previous) => ({
            ...previous,
            last_sync_at: saved.last_sync_at,
            last_sync_result: saved.last_sync_result,
            last_sync_count: saved.last_sync_count,
            enabled: saved.enabled,
            verified: saved.verified,
          }));
        })
        .catch(() => { /* The next refresh can recover transient errors. */ });
    }, 5000);
    return () => { controller.abort(); clearInterval(interval); };
  }, [mailbox.enabled, dirty, pending]);

  function edit<K extends keyof Mailbox>(field: K, value: Mailbox[K]) {
    setMailbox((previous) => ({ ...previous, [field]: value }));
    setDirty(true);
    setMessage(null);
  }

  function editAddress(address: string) {
    setMailbox((previous) => ({
      ...previous, address,
      host: /@163\.com$/i.test(address.trim()) ? "imap.163.com" : previous.host,
    }));
    setDirty(true);
    setMessage(null);
  }

  async function refresh() {
    setPending("load");
    setMessage(null);
    try {
      const saved = await relayRequest<Mailbox | null>("/mailbox");
      setMailbox(saved ?? initialMailbox);
      setSavedAccount(saved);
      onAccount(saved);
      setPassword("");
      setDirty(false);
      setLoaded(true);
    } catch (error) {
      setMessage({ ok: false, text: messageOf(error) });
    } finally {
      setPending(null);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending("save");
    setMessage(null);
    try {
      const saved = await relayRequest<Mailbox>("/mailbox", {
        method: "PUT",
        body: JSON.stringify({
          address: mailbox.address.trim(), host: mailbox.host, folder: mailbox.folder,
          since: mailbox.since, poll_minutes: mailbox.poll_minutes, password: password || null,
        }),
      });
      setMailbox(saved);
      setSavedAccount(saved);
      onAccount(saved);
      setPassword("");
      setDirty(false);
      onChanged();
      setMessage({ ok: true, text: "邮箱配置已保存，请测试连接后启动采集。" });
    } catch (error) {
      setMessage({ ok: false, text: messageOf(error) });
    } finally {
      setPending(null);
    }
  }

  async function testConnection() {
    setPending("test");
    setMessage(null);
    try {
      const { result } = await relayRequest<{ result: string }>("/mailbox/connection-test", { method: "POST" });
      const saved = await relayRequest<Mailbox | null>("/mailbox");
      setMailbox(saved ?? initialMailbox);
      setSavedAccount(saved);
      onAccount(saved);
      onChanged();
      setMessage({ ok: result === "ok", text: connectionMessages[result] ?? "无法确认连接结果。" });
    } catch (error) {
      setMessage({ ok: false, text: messageOf(error) });
    } finally {
      setPending(null);
    }
  }

  async function setSync(enabled: boolean) {
    setPending("sync");
    setMessage(null);
    try {
      const saved = await relayRequest<Mailbox>("/mailbox/sync", {
        method: "PUT", body: JSON.stringify({ enabled }),
      });
      setMailbox((previous) => ({
        ...previous,
        enabled: saved.enabled, verified: saved.verified,
        last_sync_at: saved.last_sync_at, last_sync_result: saved.last_sync_result,
        last_sync_count: saved.last_sync_count,
      }));
      onChanged();
      setMessage({
        ok: true,
        text: enabled ? "采集已启用。首批邮件归档后可在「收件档案」查看。" : "采集已暂停。",
      });
    } catch (error) {
      setMessage({ ok: false, text: messageOf(error) });
    } finally {
      setPending(null);
    }
  }

  const notice = message && (
    <p className={`notice mailbox-notice ${message.ok ? "" : "failed"}`} role={message.ok ? "status" : "alert"}>
      {message.ok && <CheckCircleOutlined aria-hidden="true" />}{message.text}
    </p>
  );

  return (
    <div className="mailbox-settings">
      <form className="page-card mailbox-card" onSubmit={(event) => void save(event)}>
        <div className="section-heading">
          <h2><MailOutlined aria-hidden="true" /> 收件采集</h2>
          <span className={`status ${mailbox.enabled ? "complete" : "queued"}`}>
            {mailbox.enabled ? "采集中" : !mailbox.has_password ? "待配置" : mailbox.verified ? "待启动" : "待验证"}
          </span>
        </div>
        <p className="field-hint">通过 IMAP 只读采集订单附件。保存变更后，需重新测试并启动采集。</p>
        {!loaded && pending === null && <button type="button" className="button" onClick={() => void refresh()}><ReloadOutlined aria-hidden="true" /> 重新读取配置</button>}
        <fieldset className="mailbox-fields" disabled={!loaded || pending !== null}>
          <label>邮箱地址
            <input type="email" autoComplete="off" value={mailbox.address} required maxLength={254}
              placeholder="edi@company.com 或 name@163.com" onChange={(event) => editAddress(event.target.value)} />
          </label>
          <label>客户端授权码
            <input type="password" autoComplete="new-password" value={password} maxLength={512}
              required={!canKeepPassword}
              placeholder={canKeepPassword ? "留空保留已保存的授权码" : "输入客户端授权码"}
              onChange={(event) => { setPassword(event.target.value); setDirty(true); setMessage(null); }} />
          </label>
          <label>IMAP 服务器
            <WorkbenchSelect label="IMAP 服务器" value={mailbox.host} options={imapServers}
              disabled={!loaded || pending !== null} onChange={(host) => edit("host", host)} />
          </label>
          <label>连接方式<input type="text" value="TLS / 993" readOnly /></label>
          <label>采集文件夹<input type="text" value={mailbox.folder} required maxLength={128} onChange={(event) => edit("folder", event.target.value)} /></label>
          <label>历史采集起始日期<input type="date" value={mailbox.since} required onChange={(event) => edit("since", event.target.value)} /></label>
          <label>轮询间隔（分钟）
            <input type="number" value={mailbox.poll_minutes || ""} min={1} max={60} required onChange={(event) => edit("poll_minutes", Number(event.target.value))} />
          </label>
        </fieldset>
        {providerMismatch && <p className="field-hint" role="status">请将 @163.com 地址与「网易 163」服务器配对。</p>}
        {notice}
        <div className="mailbox-actions">
          <button type="submit" className="button primary-button" disabled={!loaded || pending !== null || !dirty || providerMismatch}>
            {pending === "save" ? "保存中…" : "保存采集配置"}
          </button>
          <button type="button" className="button" onClick={() => void testConnection()} disabled={pending !== null || dirty || !mailbox.has_password}>
            {pending === "test" ? "测试中…" : "测试收件连接"}
          </button>
          <button type="button" className="button" onClick={() => void setSync(!mailbox.enabled)}
            disabled={pending !== null || (!mailbox.enabled && (dirty || !mailbox.verified))}>
            {pending === "sync" ? "处理中…" : mailbox.enabled ? "暂停采集" : "启动采集"}
          </button>
        </div>
      </form>
      <section className="page-card mailbox-card" aria-labelledby="mailbox-sync-heading">
        <h2 id="mailbox-sync-heading"><HistoryOutlined aria-hidden="true" /> 最近一次同步</h2>
        {mailbox.last_sync_at && mailbox.last_sync_result
          ? <>
            <p className={`notice mailbox-notice ${mailbox.last_sync_result === "ok" ? "" : "failed"}`}>
              {mailbox.last_sync_result === "ok" && <CheckCircleOutlined aria-hidden="true" />}
              {syncMessages[mailbox.last_sync_result] ?? "同步暂不可用"}
            </p>
            <dl className="mailbox-sync-details">
              <div>
                <dt>同步时间</dt>
                <dd><time dateTime={mailbox.last_sync_at}>{chinaTime.format(new Date(mailbox.last_sync_at))}</time></dd>
              </div>
              {mailbox.last_sync_count !== null && <div>
                <dt>检查邮件</dt>
                <dd>{mailbox.last_sync_count} 封</dd>
              </div>}
            </dl>
          </>
          : <p className="field-hint">暂无同步记录。</p>}
      </section>
    </div>
  );
}
