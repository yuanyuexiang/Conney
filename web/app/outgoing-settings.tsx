"use client";

import { LinkOutlined, PlusOutlined, SendOutlined, TeamOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useState } from "react";
import type { CredentialSource, OutgoingConfig, OutgoingHost, RecipientGroup } from "./dispatch-api";
import { MailboxSettings, type CollectionAccount } from "./mailbox-settings";
import { messageOf, RelayRequestError, relayRequest } from "./relay-api";
import { WorkbenchSelect } from "./workbench-select";

const servers: { value: OutgoingHost; label: string }[] = [
  { value: "smtp.263.net", label: "263 企业邮箱 · smtp.263.net" },
  { value: "smtpw.263.net", label: "263 海外 · smtpw.263.net" },
  { value: "smtp.163.com", label: "网易 163 · smtp.163.com" },
];
const split = (text: string) => text.split(/[\s,，;；]+/).map(value => value.trim()).filter(Boolean);
const smtpHosts: Record<CollectionAccount["host"], OutgoingHost> = {
  "imap.263.net": "smtp.263.net",
  "imapw.263.net": "smtpw.263.net",
  "imap.163.com": "smtp.163.com",
};

function OutgoingForm({ account, config, onUpdate }: {
  account: CollectionAccount | null | undefined;
  config: OutgoingConfig | null;
  onUpdate: (value: OutgoingConfig) => void;
}) {
  const [source, setSource] = useState<CredentialSource>(config?.credential_source ?? "incoming");
  const [separateHost, setSeparateHost] = useState<OutgoingHost>(config?.host ?? "smtp.263.net");
  const [separateAddress, setSeparateAddress] = useState(config?.address ?? "");
  const [domains, setDomains] = useState(config?.domains.join(", ") ?? "");
  const [testRecipient, setTestRecipient] = useState(config?.test_recipient ?? "");
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<"save" | "test">();
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const shared = source === "incoming";
  const host = shared && account ? smtpHosts[account.host] : separateHost;
  const address = shared ? account?.address ?? "" : separateAddress;
  const linked = Boolean(account?.has_password && account.address);
  const needsRelink = shared && config?.credential_source === "incoming" &&
    (!config.credential_available || config.address !== address || config.host !== host);
  const hasChanges = dirty || (needsRelink && linked);
  const testMode = host === "smtp.163.com";
  const keepPassword = config?.credential_source === "separate" &&
    config.address === address.trim() && config.host === host;

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (shared && !linked) return;
    const fields = new FormData(event.currentTarget);
    setBusy("save");
    setError("");
    setNotice("");
    try {
      const saved = await relayRequest<OutgoingConfig>("/mailbox/outgoing", {
        method: "PUT",
        body: JSON.stringify({
          address: address.trim(), host, credential_source: source,
          password: shared ? null : String(fields.get("password") ?? "") || null,
          domains: testMode ? [] : split(domains),
          test_recipient: testMode ? testRecipient.trim() : null,
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
      const saved = await relayRequest<OutgoingConfig | null>("/mailbox/outgoing");
      if (saved) onUpdate(saved);
      if (result === "ok") setNotice("SMTP 登录验证通过，可以发件。");
      else setError(messageOf(new RelayRequestError(422, result)));
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(undefined); }
  }

  return <form className="page-card mailbox-card" onSubmit={event => void save(event)} onChange={() => {
    setDirty(true); setError(""); setNotice("");
  }}>
    <div className="section-heading">
      <h2><SendOutlined aria-hidden="true" /> 交付发件</h2>
      <span className={`status ${config?.verified && !hasChanges && !needsRelink ? "complete" : "queued"}`}>
        {hasChanges ? "待保存" : !config ? "待配置" : needsRelink ? "待重新关联" : config.verified ? "登录已验证" : "待验证"}
      </span>
    </div>
    <fieldset className="mailbox-source" disabled={Boolean(busy)}>
      <legend>发件账号</legend>
      <label>
        <input type="radio" name="mailbox_source" checked={shared} onChange={() => setSource("incoming")} />
        <span><strong>使用采集邮箱</strong><small>共用邮箱地址和客户端授权码</small></span>
      </label>
      <label>
        <input type="radio" name="mailbox_source" checked={!shared} onChange={() => setSource("separate")} />
        <span><strong>单独配置发件邮箱</strong><small>填写发件账号与授权码</small></span>
      </label>
    </fieldset>
    {shared && <div className="mailbox-linked-account">
      <LinkOutlined aria-hidden="true" />
      <div>{linked
        ? <><strong>{address}</strong><span>{host} · TLS / 465</span></>
        : <span>{account === undefined ? "正在读取采集邮箱…" : "请先保存采集邮箱，发件会使用已保存的授权码。"}</span>}</div>
    </div>}
    {needsRelink && linked && <p className="field-hint">采集账号已变更。保存发件配置后，重新测试发件登录。</p>}
    <fieldset className="mailbox-fields" disabled={Boolean(busy)}>
      {!shared && <>
      <label>发件邮箱<input type="email" value={separateAddress} required autoComplete="off" maxLength={254}
        onChange={event => {
          const value = event.target.value;
          setSeparateAddress(value);
          if (/@163\.com$/i.test(value.trim())) setSeparateHost("smtp.163.com");
          else if (separateHost === "smtp.163.com" && value.includes("@")) setSeparateHost("smtp.263.net");
        }} /></label>
      <label>发件授权码<input key={config?.revision ?? "initial"} name="password" type="password"
        maxLength={512} required={!keepPassword} autoComplete="new-password"
        placeholder={keepPassword ? "留空保留已保存的授权码" : "输入 SMTP 客户端授权码"} /></label>
      <label>SMTP 服务器<WorkbenchSelect label="SMTP 服务器" value={host} options={servers}
        onChange={value => { setSeparateHost(value); setDirty(true); setNotice(""); setError(""); }} disabled={Boolean(busy)} /></label>
      <label>连接方式<input value="TLS / 465" readOnly /></label>
      </>}
      {(!shared || linked) && (testMode
        ? <label className="dispatch-wide">唯一测试收件地址<input key="test_recipient" type="email"
          name="test_recipient" required value={testRecipient} onChange={event => setTestRecipient(event.target.value)} maxLength={254} />
          <span className="field-hint">163 测试模式只允许发到这个地址，请使用测试附件。</span></label>
        : <label className="dispatch-wide">内部收件域名<input key="domains" name="domains" required
          value={domains} onChange={event => setDomains(event.target.value)} placeholder="例如 company.com，多个域名用逗号分隔" /></label>)}
    </fieldset>
    {notice && <p className="notice" role="status">{notice}</p>}
    {error && <p className="notice failed" role="alert">{error}</p>}
    <div className="mailbox-actions">
      <button type="submit" className="button primary-button" disabled={Boolean(busy) || !hasChanges || (shared && !linked)}>
        {busy === "save" ? "保存中…" : "保存发件配置"}
      </button>
      <button type="button" className="button" disabled={!config?.credential_available || hasChanges || needsRelink || Boolean(busy)} onClick={() => void test()}>
        {busy === "test" ? "验证中…" : "测试发件登录"}
      </button>
    </div>
  </form>;
}

function RecipientGroups({ groups, config, onChange }: {
  groups: RecipientGroup[]; config: OutgoingConfig | null; onChange: (value: RecipientGroup) => void;
}) {
  const [editing, setEditing] = useState<RecipientGroup | "new">();
  const [addressesText, setAddressesText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [addressError, setAddressError] = useState("");
  const saved = typeof editing === "object" ? editing : undefined;
  const testAddress = config?.test_recipient;
  const fixedTestAddress = testAddress && (
    !saved || (saved.addresses.length === 1 && saved.addresses[0].toLowerCase() === testAddress.toLowerCase())
  ) ? testAddress : undefined;
  const allowedAddress = (address: string) => testAddress
    ? address.toLowerCase() === testAddress.toLowerCase()
    : Boolean(config?.domains.includes(address.split("@").at(-1)?.toLowerCase() ?? ""));
  const scopeHint = testAddress
    ? `163 测试模式只允许发到 ${testAddress}。`
    : config?.domains.length ? `允许使用的邮箱域名为 ${config.domains.join("、")}。` : "";

  function open(value: RecipientGroup | "new") {
    setEditing(value);
    setAddressesText(value === "new" ? "" : value.addresses.join("\n"));
    setError("");
    setAddressError("");
  }

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    setAddressError("");
    try {
      const group = await relayRequest<RecipientGroup>(`/mailbox/recipient-groups${saved ? `/${saved.id}` : ""}`, {
        method: saved ? "PUT" : "POST",
        body: JSON.stringify({
          name: form.get("group_name"), addresses: fixedTestAddress ? [fixedTestAddress] : split(addressesText),
          active: form.has("active"), ...(saved ? { revision: saved.revision } : {}),
        }),
      });
      onChange(group);
      setEditing(undefined);
    } catch (reason) {
      if (reason instanceof RelayRequestError && reason.code === "dispatch_address_not_allowed") {
        setAddressError(scopeHint || reason.message);
      } else if (reason instanceof RelayRequestError && reason.code === "dispatch_group_invalid_addresses") {
        setAddressError(reason.message);
      } else setError(messageOf(reason));
    }
    finally { setBusy(false); }
  }

  return <section className="page-card mailbox-card">
    <div className="section-heading">
      <h2><TeamOutlined aria-hidden="true" /> 部门收件组</h2>
      <button type="button" className="button" disabled={!config} onClick={() => open("new")}><PlusOutlined aria-hidden="true" /> 新增收件组</button>
    </div>
    {!config && <p className="field-hint">保存发件配置后可添加部门收件邮箱。</p>}
    {config && !groups.length && <p className="empty">暂无部门收件组</p>}
    {groups.length > 0 && <ul className="dispatch-stack-list">
      {groups.map(group => {
        const allowed = group.addresses.every(allowedAddress);
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
        <label><span id="recipient-group-email-label">收件邮箱</span>
          {fixedTestAddress
            ? <input type="email" value={fixedTestAddress} readOnly aria-labelledby="recipient-group-email-label"
              aria-describedby="recipient-group-scope" />
            : <textarea rows={3} required value={addressesText} onChange={event => {
              setAddressesText(event.target.value);
              setAddressError("");
            }} placeholder="例如 production@example.com，每行一个邮箱" aria-invalid={Boolean(addressError)}
              aria-labelledby="recipient-group-email-label"
              aria-describedby={addressError ? "recipient-group-address-error" : "recipient-group-scope"} />}
        </label>
        {scopeHint && <p className="field-hint" id="recipient-group-scope">{scopeHint}</p>}
        {testAddress && !fixedTestAddress && <div>
          <button type="button" className="text-button" disabled={busy} onClick={() => {
            setAddressesText(testAddress); setAddressError("");
          }}>填入当前测试地址</button>
        </div>}
        {addressError && <p id="recipient-group-address-error" className="notice failed" role="alert">{addressError}</p>}
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

function OutgoingSettings({ account, accountVersion }: { account: CollectionAccount | null | undefined; accountVersion: number }) {
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
  }, [reload, accountVersion]);
  if (config === undefined) return error ? <p className="notice failed" role="alert">
    {error}<button type="button" className="button" onClick={() => setReload(value => value + 1)}>重试</button>
  </p> : <p className="empty">正在读取发件设置…</p>;
  return <div className="mailbox-settings outgoing-settings">
    <OutgoingForm account={account} config={config} onUpdate={setConfig} />
    <RecipientGroups groups={groups} config={config} onChange={value => setGroups(previous =>
      [...previous.filter(item => item.id !== value.id), value].sort((a, b) => a.name.localeCompare(b.name, "zh-CN")),
    )} />
  </div>;
}

export function MailConfiguration({ onChanged }: { onChanged: () => void }) {
  const [account, setAccount] = useState<CollectionAccount | null>();
  const [accountVersion, setAccountVersion] = useState(0);
  return <div className="mailbox-workspace">
    <MailboxSettings onAccount={setAccount} onChanged={() => {
      setAccountVersion(version => version + 1);
      onChanged();
    }} />
    <OutgoingSettings account={account} accountVersion={accountVersion} />
  </div>;
}
