"use client";

import { MailOutlined, PlusOutlined, TeamOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useState } from "react";
import type { OutgoingConfig, OutgoingHost, RecipientGroup } from "./dispatch-api";
import { MailboxSettings } from "./mailbox-settings";
import { messageOf, RelayRequestError, relayRequest } from "./relay-api";
import { WorkbenchSelect } from "./workbench-select";

const servers: { value: OutgoingHost; label: string }[] = [
  { value: "smtp.263.net", label: "263 企业邮箱 · smtp.263.net" },
  { value: "smtpw.263.net", label: "263 海外 · smtpw.263.net" },
  { value: "smtp.163.com", label: "网易 163 · smtp.163.com" },
];
const split = (text: string) => text.split(/[\s,，;；]+/).map(value => value.trim()).filter(Boolean);

function OutgoingForm({ config, onUpdate }: {
  config: OutgoingConfig | null;
  onUpdate: (value: OutgoingConfig) => void;
}) {
  const [host, setHost] = useState<OutgoingHost>(config?.host ?? "smtp.263.net");
  const [address, setAddress] = useState(config?.address ?? "");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"save" | "test">();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const testMode = host === "smtp.163.com";
  const keepPassword = config?.address === address.trim() && config.host === host;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const saved = await relayRequest<OutgoingConfig>("/mailbox/outgoing", {
        method: "PUT",
        body: JSON.stringify({
          address, host,
          password: String(fields.get("password") ?? "") || null,
          domains: testMode ? [] : split(String(fields.get("domains") ?? "")),
          test_recipient: testMode ? fields.get("test_recipient") : null,
        }),
      });
      setNotice("已保存，请测试发件登录。");
      setDirty(false);
      onUpdate(saved);
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(undefined); }
  }

  async function test() {
    setBusy("test");
    setError("");
    setNotice("");
    try {
      const { result } = await relayRequest<{ result: string }>("/mailbox/outgoing/connection-test", { method: "POST" });
      if (config) onUpdate({ ...config, verified: result === "ok" });
      if (result === "ok") setNotice("SMTP 登录验证通过，可以发件。");
      else setError(messageOf(new RelayRequestError(422, result)));
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(undefined); }
  }

  return <form className="page-card mailbox-card" onSubmit={event => void save(event)} onChange={() => {
    setDirty(true); setError(""); setNotice("");
  }}>
    <div className="section-heading">
      <h2><MailOutlined aria-hidden="true" /> SMTP 发件邮箱</h2>
      <span className={`status ${config?.verified && !dirty ? "complete" : "queued"}`}>
        {!config ? "待配置" : config.verified && !dirty ? "登录已验证" : "待验证"}
      </span>
    </div>
    <fieldset className="mailbox-fields" disabled={Boolean(busy)}>
      <label>发件邮箱<input type="email" value={address} required autoComplete="off" maxLength={254}
        onChange={event => {
          const value = event.target.value;
          setAddress(value);
          if (/@163\.com$/i.test(value.trim())) setHost("smtp.163.com");
          else if (host === "smtp.163.com" && value.includes("@")) setHost("smtp.263.net");
        }} /></label>
      <label>发件授权码<input key={config?.revision ?? "initial"} name="password" type="password"
        maxLength={512} required={!keepPassword} autoComplete="new-password"
        placeholder={keepPassword ? "留空保留已保存的授权码" : "输入 SMTP 客户端授权码"} /></label>
      <label>SMTP 服务器<WorkbenchSelect label="SMTP 服务器" value={host} options={servers}
        onChange={value => { setHost(value); setDirty(true); setNotice(""); setError(""); }} disabled={Boolean(busy)} /></label>
      <label>连接方式<input value="TLS / 465" readOnly /></label>
      {testMode
        ? <label className="dispatch-wide">唯一测试收件地址<input key="test_recipient" type="email"
          name="test_recipient" required defaultValue={config?.test_recipient ?? ""} maxLength={254} />
          <span className="field-hint">163 测试模式只允许发到这个地址，请使用测试附件。</span></label>
        : <label className="dispatch-wide">内部收件域名<input key="domains" name="domains" required
          defaultValue={config?.domains.join(", ") ?? ""} placeholder="例如 company.com，多个域名用逗号分隔" /></label>}
    </fieldset>
    {notice && <p className="notice" role="status">{notice}</p>}
    {error && <p className="notice failed" role="alert">{error}</p>}
    <div className="mailbox-actions">
      <button type="submit" className="button primary-button" disabled={Boolean(busy) || !dirty}>
        {busy === "save" ? "保存中…" : "保存发件配置"}
      </button>
      <button type="button" className="button" disabled={!config || dirty || Boolean(busy)} onClick={() => void test()}>
        {busy === "test" ? "验证中…" : "测试发件登录"}
      </button>
    </div>
  </form>;
}

function RecipientGroups({ groups, config, onChange }: {
  groups: RecipientGroup[]; config: OutgoingConfig | null; onChange: (value: RecipientGroup) => void;
}) {
  const [editing, setEditing] = useState<RecipientGroup | "new">();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const saved = typeof editing === "object" ? editing : undefined;

  function open(value: RecipientGroup | "new") {
    setEditing(value);
    setError("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const group = await relayRequest<RecipientGroup>(`/mailbox/recipient-groups${saved ? `/${saved.id}` : ""}`, {
        method: saved ? "PUT" : "POST",
        body: JSON.stringify({
          name: form.get("group_name"), addresses: split(String(form.get("addresses") ?? "")),
          active: form.has("active"), ...(saved ? { revision: saved.revision } : {}),
        }),
      });
      onChange(group);
      setEditing(undefined);
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(false); }
  }

  return <section className="page-card mailbox-card">
    <div className="section-heading">
      <h2><TeamOutlined aria-hidden="true" /> 部门收件组</h2>
      <button type="button" className="button" disabled={!config} onClick={() => open("new")}><PlusOutlined aria-hidden="true" /> 新增收件组</button>
    </div>
    {!config && <p className="field-hint">保存发件配置后可设置部门地址。</p>}
    {config && !groups.length && <p className="empty">暂无部门收件组</p>}
    {groups.length > 0 && <ul className="dispatch-stack-list">
      {groups.map(group => {
        const allowed = config && group.addresses.every(address => config.test_recipient
          ? address.toLowerCase() === config.test_recipient.toLowerCase()
          : config.domains.includes(address.split("@").at(-1) ?? ""));
        return <li key={group.id}>
          <div><strong>{group.name}</strong>{" "}
            <span className={`status ${group.active && allowed ? "complete" : "queued"}`}>
              {group.active ? allowed ? "可选" : "超出收件范围" : "已停用"}
            </span>
            <p className="field-hint">{group.addresses.join("、")}</p>
          </div>
          <button type="button" className="text-button" onClick={() => open(group)} aria-label={`编辑${group.name}`}>编辑</button>
        </li>;
      })}
    </ul>}
    {editing && <Modal open centered title={saved ? "编辑部门收件组" : "新增部门收件组"}
      footer={null} onCancel={() => setEditing(undefined)} closable={!busy} keyboard={!busy} mask={{ closable: false }}>
      <form key={saved?.id ?? "new"} className="dispatch-form" onSubmit={event => void save(event)}>
        <label>部门名称<input name="group_name" maxLength={64} required defaultValue={saved?.name}
          placeholder="例如生产部" /></label>
        <label>部门地址<textarea name="addresses" rows={3} required defaultValue={saved?.addresses.join("\n")}
          placeholder="每行填写一个邮箱地址" /></label>
        <label className="dispatch-choice"><input type="checkbox" name="active" defaultChecked={saved?.active ?? true} />启用收件组</label>
        {error && <p className="notice failed" role="alert">{error}</p>}
        <div className="dispatch-actions">
          <button type="button" className="button" disabled={busy} onClick={() => setEditing(undefined)}>取消</button>
          <button type="submit" className="button primary-button" disabled={busy}>{busy ? "保存中…" : "保存收件组"}</button>
        </div>
      </form>
    </Modal>}
  </section>;
}

function OutgoingSettings() {
  const [config, setConfig] = useState<OutgoingConfig | null>();
  const [groups, setGroups] = useState<RecipientGroup[]>([]);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const options = { signal: controller.signal };
    Promise.all([
      relayRequest<OutgoingConfig | null>("/mailbox/outgoing", options),
      relayRequest<RecipientGroup[]>("/mailbox/recipient-groups", options),
    ]).then(([saved, departments]) => {
      if (controller.signal.aborted) return;
      setConfig(saved); setGroups(departments); setError("");
    }).catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [reload]);
  if (config === undefined) return error ? <p className="notice failed" role="alert">
    {error}<button type="button" className="button" onClick={() => setReload(value => value + 1)}>重试</button>
  </p> : <p className="empty">正在读取发件设置…</p>;
  return <div className="mailbox-settings outgoing-settings">
    <OutgoingForm config={config} onUpdate={setConfig} />
    <RecipientGroups groups={groups} config={config} onChange={value => setGroups(previous =>
      [...previous.filter(item => item.id !== value.id), value].sort((a, b) => a.name.localeCompare(b.name, "zh-CN")),
    )} />
  </div>;
}

export function MailConfiguration({ onChanged }: { onChanged: () => void }) {
  const [outgoing, setOutgoing] = useState(false);
  return <>
    <div className="delivery-view-tabs dispatch-settings-tabs" aria-label="邮箱配置方式">
      <button type="button" aria-pressed={!outgoing} onClick={() => setOutgoing(false)}>收件采集</button>
      <button type="button" aria-pressed={outgoing} onClick={() => setOutgoing(true)}>发件与部门</button>
    </div>
    {outgoing ? <OutgoingSettings /> : <MailboxSettings onChanged={onChanged} />}
  </>;
}
