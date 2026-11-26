"use client";

import { CheckCircleOutlined, DownloadOutlined, HistoryOutlined, SendOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { deliveryTemplates, type DeliveryDraft } from "./delivery-api";
import type { DispatchRecord, RecipientState, Release, ReleaseBlocker, ReleasePreview, SendOptions } from "./dispatch-api";
import { messageOf, RelayRequestError, relayRequest } from "./relay-api";
import { chinaTime } from "./workbench-status";

const beijingDate = new Intl.DateTimeFormat("sv-SE", {
  timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit",
});
const blockerNames: Record<string, string> = {
  missing: "未匹配物料", ambiguous: "物料匹配冲突", supplier_mismatch: "供应商料号冲突",
  code_review: "数量代码待核对", period_review: "日期区间待核对", transport_missing: "缺少发货安排",
};
const sendStates: Record<RecipientState, { label: string; tone: string }> = {
  queued: { label: "排队中", tone: "queued" },
  sending: { label: "发送中", tone: "queued" },
  accepted: { label: "邮件服务器已接收", tone: "complete" },
  failed: { label: "发送失败", tone: "failed" },
  uncertain: { label: "待核实", tone: "review" },
};
const releasedFile = (release: Release) =>
  `/api/v1/delivery/${release.run_id}/releases/${release.version}/workbook.xlsx`;
const deliverySends = (release: Release) =>
  `/delivery/${release.run_id}/releases/${release.version}/dispatches`;

function ConfirmationIssues({ rows, label }: { rows: ReleaseBlocker[]; label: string }) {
  return <div className="table-scroll dispatch-blockers">
    <table aria-label={label}><thead><tr><th>问题</th><th>客户料号 / 订单</th><th>来源</th><th>交货日期</th></tr></thead>
      <tbody>{rows.map((item, index) => <tr key={index}>
        <td>{item.reason ?? blockerNames[item.kind] ?? "待核验"}</td>
        <td>{item.customer_item ?? "—"} / {item.order_no ?? "—"}</td>
        <td>{item.cell ?? "—"}</td><td>{item.requested_date ?? "—"}</td>
      </tr>)}</tbody>
    </table>
  </div>;
}

function ConfirmDelivery({ draft, onClose, onConfirmed, onFix, onStale, onAlreadyConfirmed }: {
  draft: DeliveryDraft; onClose: () => void; onConfirmed: (release: Release) => void;
  onFix: (mode: "review" | "shipment") => void; onStale: () => void; onAlreadyConfirmed: () => void;
}) {
  const [cutoff, setCutoff] = useState(() => beijingDate.format(new Date()));
  const [preview, setPreview] = useState<{ cutoff: string; revision: number; data: ReleasePreview }>();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const drx = draft.template === "drx_delfor";
  const details = preview?.cutoff === cutoff && preview.revision === draft.revision ? preview.data : undefined;
  const blocked = details && (details.pending_mappings + details.pending_reviews + details.pending_shipments > 0);

  useEffect(() => {
    if (drx && !cutoff) return;
    const request = new AbortController();
    const query = new URLSearchParams({ revision: String(draft.revision) });
    if (drx) query.set("ship_through", cutoff);
    relayRequest<ReleasePreview>(`/delivery/${draft.id}/release-preview?${query}`, { signal: request.signal })
      .then(data => {
        if (!request.signal.aborted) {
          setPreview({ cutoff, revision: draft.revision, data }); setAcknowledged(false); setError("");
        }
      })
      .catch(reason => {
        if (request.signal.aborted) return;
        if (reason instanceof RelayRequestError && reason.code === "draft_changed") onStale();
        setError(messageOf(reason));
      });
    return () => request.abort();
  }, [draft.id, draft.revision, cutoff, drx, onStale]);

  async function confirm(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!details || blocked || (details.retained_issues > 0 && !acknowledged)) return;
    setBusy(true);
    setError("");
    try {
      const release = await relayRequest<Release>(`/delivery/${draft.id}/releases`, {
        method: "POST", body: JSON.stringify({
          revision: draft.revision,
          ...(drx ? { ship_through: cutoff } : { acknowledge_retained: acknowledged }),
        }),
      });
      onConfirmed(release);
    } catch (reason) {
      if (reason instanceof RelayRequestError && reason.code === "draft_changed") onStale();
      if (reason instanceof RelayRequestError && reason.code === "release_already_confirmed") {
        onAlreadyConfirmed();
        return;
      }
      setError(messageOf(reason));
    } finally { setBusy(false); }
  }

  return <Modal title="确认交付" open centered width={690} footer={null}
    onCancel={onClose} closable={!busy} keyboard={!busy} mask={{ closable: false }}>
    <form className="dispatch-form" onSubmit={event => void confirm(event)}>
      <p className="field-hint">报文 {draft.document_no ?? "—"} · 草稿版本 {draft.revision} · 规则 {draft.rule_version}</p>
      {drx ? <>
        <label className="dispatch-date">核验交货截至日<input type="date" value={cutoff} required disabled={busy}
          onChange={event => { setCutoff(event.target.value); setError(""); setPreview(undefined); }} /></label>
        <p className="field-hint">截至当日（含逾期）的正式需求须已安排发货。之后的需求和区间预测随确认版保留。</p>
      </> : <p className="field-hint">整批核验 {deliveryTemplates[draft.template].title} 的物料映射和需求计算。通过后生成可下载、可分发的确认版。</p>}
      {(!drx || cutoff) && !details && !error && <p className="field-hint" role="status">正在检查草稿…</p>}
      {details && <>
        <dl className="dispatch-counters">
          <div><dt>未匹配物料</dt><dd>{details.pending_mappings}</dd></div>
          <div><dt>待核对需求</dt><dd>{details.pending_reviews}</dd></div>
          {drx ? <>
            <div><dt>范围内待安排</dt><dd>{details.pending_shipments}</dd></div>
            <div><dt>已安排发货</dt><dd>{details.scheduled}</dd></div>
          </> : <>
            <div><dt>已计算数量记录</dt><dd>{details.calculated_quantities}</dd></div>
            <div><dt>随确认版保留</dt><dd>{details.retained_issues}</dd></div>
          </>}
        </dl>
        {details.future_unscheduled > 0 && <p className="field-hint">截至日之后还有 {details.future_unscheduled} 条待安排需求。</p>}
        {blocked && <div className="dispatch-actions dispatch-fix">
          {details.pending_mappings + details.pending_reviews > 0 &&
            <button type="button" className="button" onClick={() => onFix("review")}>查看待核对</button>}
          {details.pending_shipments > 0 &&
            <button type="button" className="button" onClick={() => onFix("shipment")}>查看待安排发货</button>}
        </div>}
        {details.blockers.length > 0 && <ConfirmationIssues rows={details.blockers} label="阻止本次确认的问题" />}
        {details.pending_mappings + details.pending_reviews + details.pending_shipments > details.blockers.length &&
          <p className="field-hint">显示前 {details.blockers.length} 项，完整清单可在对应视图查看。</p>}
        {details.retained_issues > 0 && <>
          <p className="field-hint">特殊日期及跨周区间保留在确认版的「待核对」工作表，供后续人工处理。</p>
          <ConfirmationIssues rows={details.retained_details} label="随确认版保留的记录" />
          {details.retained_issues > details.retained_details.length && <p className="field-hint">
            显示前 {details.retained_details.length} 项，完整清单见草稿的「待核对」。
          </p>}
          <label className="dispatch-choice"><input type="checkbox" checked={acknowledged} disabled={busy}
            onChange={event => setAcknowledged(event.target.checked)} />我已核对这些记录，并确认按原值或区间保留</label>
        </>}
      </>}
      {error && <p className="notice failed" role="alert">{error}</p>}
      <div className="dispatch-actions">
        <button type="button" className="button" onClick={onClose} disabled={busy}>取消</button>
        <button type="submit" className="button primary-button" disabled={busy || !details || blocked || (details.retained_issues > 0 && !acknowledged)}>
          {busy ? "正在生成…" : "确认并生成版本"}
        </button>
      </div>
    </form>
  </Modal>;
}

function useRequestKey() {
  const previous = useRef<{ content: string; key: string }>(undefined);
  return (payload: object) => {
    const content = JSON.stringify(payload);
    if (previous.current?.content !== content) previous.current = { content, key: crypto.randomUUID() };
    return { ...payload, request_id: previous.current.key };
  };
}

function SendDelivery({ release, onClose, onSent }: {
  release: Release; onClose: () => void; onSent: () => void;
}) {
  const [options, setOptions] = useState<SendOptions>();
  const [selected, setSelected] = useState<number[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [reload, setReload] = useState(0);
  const withRequestKey = useRequestKey();
  const path = deliverySends(release);
  const title = release.template === "drx_delfor" ? "德科" : deliveryTemplates[release.template].title;
  const scope = release.ship_through ? `，核验交货截至 ${release.ship_through}` : "";
  const all = [...new Set(options?.groups.filter(({ id }) => selected.includes(id)).flatMap(group => group.addresses))].sort();
  const addresses = all.filter(address => !options?.attempted_addresses.includes(address));

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<SendOptions>(`${path}/options`, { signal: controller.signal })
      .then(values => { if (!controller.signal.aborted) setOptions(values); })
      .catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [path, reload]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!options?.mailbox_revision || !addresses.length) return;
    const data = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await relayRequest<DispatchRecord>(path, { method: "POST", body: JSON.stringify(withRequestKey({
        mailbox_revision: options.mailbox_revision, group_ids: [...selected].sort((a, b) => a - b),
        addresses, subject: data.get("subject"), body: data.get("body"),
      })) });
      onSent();
    } catch (reason) {
      setError(messageOf(reason));
    } finally { setBusy(false); }
  }

  return <Modal open centered width={640} title={`邮件发送 · V${release.version}`} footer={null}
    onCancel={onClose} keyboard={!busy} closable={!busy} mask={{ closable: false }}>
    <form className="dispatch-form" onSubmit={event => void submit(event)}>
      {!options && !error && <p className="field-hint" role="status">正在读取发件配置…</p>}
      {options && !options.verified && <p className="notice review">请在「邮箱配置 → 发件与部门」完成 SMTP 登录测试。</p>}
      {options?.test_mode && <p className="notice review">163 测试模式，仅发送至指定测试地址。</p>}
      {options && <>
        <dl className="dispatch-meta">
          <div><dt>发件人</dt><dd>{options.sender ?? "未配置"}</dd></div>
          <div><dt>附件</dt><dd><a href={releasedFile(release)}>{release.filename}</a></dd></div>
        </dl>
        <fieldset className="dispatch-groups" disabled={busy || !options.verified}>
          <legend>发送部门</legend>
          {options.groups.map(group => <label key={group.id} className="dispatch-choice">
            <input type="checkbox" checked={selected.includes(group.id)} onChange={event =>
              setSelected(previous => event.target.checked ? [...previous, group.id] : previous.filter(id => id !== group.id))
            } />{group.name}
          </label>)}
          {options.verified && !options.groups.length && <p className="field-hint">请先在邮箱配置添加可用的部门收件组。</p>}
        </fieldset>
        {selected.length > 0 && <div className="dispatch-addresses">
          <strong>实际收件地址（{addresses.length}）</strong>
          <p>{addresses.join("、") || "所选部门地址均已有记录。"}</p>
          {all.length > addresses.length && <small>已有发送记录的地址已跳过，失败地址可从发送记录补发。</small>}
        </div>}
      </>}
      <label>邮件主题<input name="subject" required maxLength={180} disabled={busy}
        defaultValue={`${title}交付计划 · 报文 ${release.document_no ?? release.run_id} · V${release.version}`} /></label>
      {release.version > 1 && <p className="field-hint">正文开头将注明「本邮件附件 V{release.version} 替代 V{release.version - 1}，请以本版为准。」</p>}
      <label>邮件正文<textarea name="body" rows={4} required maxLength={4000} disabled={busy}
        defaultValue={`各位好，\n请查收本次${title}交付计划${scope}。\n谢谢。`} /></label>
      {error && <p className="notice failed" role="alert">{error}
        <button type="button" className="button" onClick={() => {
          setError(""); setOptions(undefined); setSelected([]); setReload(value => value + 1);
        }}>刷新收件设置</button>
      </p>}
      <div className="dispatch-actions">
        <button type="button" className="button" disabled={busy} onClick={onClose}>取消</button>
        <button type="submit" className="button primary-button" disabled={busy || !options?.verified || !addresses.length}>
          <SendOutlined aria-hidden="true" /> {busy ? "提交中…" : "确认发送"}
        </button>
      </div>
    </form>
  </Modal>;
}

function SendHistory({ release, canRetry, onClose, newlyQueued }: {
  release: Release; canRetry: boolean; onClose: () => void; newlyQueued: boolean;
}) {
  const [records, setRecords] = useState<DispatchRecord[]>();
  const [cursors, setCursors] = useState<number[]>([]);
  const [target, setTarget] = useState<DispatchRecord>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState(newlyQueued ? "已加入发送队列。" : "");
  const [refresh, setRefresh] = useState(0);
  const withRequestKey = useRequestKey();
  const path = deliverySends(release);
  const cursor = cursors.at(-1);

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    async function read() {
      if (inFlight) return;
      inFlight = true;
      try {
        const values = await relayRequest<DispatchRecord[]>(cursor ? `${path}?before=${cursor}` : path, { signal: controller.signal });
        if (!controller.signal.aborted) setRecords(values);
      } catch (reason) { if (!controller.signal.aborted) setError(messageOf(reason)); }
      finally { inFlight = false; }
    }
    void read();
    const timer = window.setInterval(() => { if (!document.hidden && !target) void read(); }, 4000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, [path, cursor, refresh, target]);

  const retryAddresses = target?.recipients.filter(item => item.retryable).map(item => item.address) ?? [];

  async function retry() {
    if (!target || !retryAddresses.length) return;
    setBusy(true);
    setNotice("");
    setError("");
    try {
      const options = await relayRequest<SendOptions>(`${path}/options`);
      if (!options.mailbox_revision) throw new RelayRequestError(409, "outgoing_not_verified");
      await relayRequest(`${path}/${target.id}/retry`, { method: "POST", body: JSON.stringify(withRequestKey({
        mailbox_revision: options.mailbox_revision, addresses: [...retryAddresses].sort(),
      })) });
      setTarget(undefined);
      setCursors([]);
      setRefresh(value => value + 1);
      setNotice("失败地址已重新排队。");
    } catch (reason) { setError(messageOf(reason)); }
    finally { setBusy(false); }
  }

  return <Modal open centered width={760} title={`发送记录 · V${release.version}`}
    footer={<button type="button" className="button" disabled={busy} onClick={onClose}>关闭</button>}
    onCancel={onClose} keyboard={!busy} closable={!busy} mask={{ closable: false }}>
    <div className="dispatch-history">
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <p className="notice failed" role="alert">{error}</p>}
      {target && <section className="dispatch-retry" aria-label="确认补发">
        <strong>补发给 {retryAddresses.length} 个失败地址</strong>
        <p>{retryAddresses.join("、")}</p>
        <div className="dispatch-actions">
          <button type="button" className="button" disabled={busy} onClick={() => { setTarget(undefined); setError(""); }}>取消补发</button>
          <button type="button" className="button primary-button" disabled={busy} onClick={() => void retry()}>确认补发</button>
        </div>
      </section>}
      {records?.length === 0 && <p className="empty">此版本暂无发送记录</p>}
      {!records && !error && <p className="empty" role="status">正在读取发送记录…</p>}
      {records?.map(record => <article key={record.id} className="dispatch-record">
        <div className="section-heading"><strong>{record.subject}</strong>
          {canRetry && record.recipients.some(item => item.retryable) &&
            <button type="button" className="text-button" disabled={busy} onClick={() => { setError(""); setTarget(record); }}>补发失败地址</button>}
        </div>
        <p className="field-hint">由 {record.created_by} 提交 · {chinaTime.format(new Date(record.created_at))}
          {record.retry_of && ` · 补发 #${record.retry_of}`}</p>
        <p className="field-hint">发件人 {record.sender} · 记录 #{record.id}</p>
        <ul className="dispatch-stack-list">
          {record.recipients.map(item => <li key={item.id}>
            <div><strong>{item.address}</strong><p className="field-hint">{item.departments.join("、")}</p>
              {item.detail && <p className="field-hint">{messageOf(new RelayRequestError(409, item.detail))}</p>}
              {item.finished_at && <p className="field-hint">{chinaTime.format(new Date(item.finished_at))}</p>}
              {item.state === "uncertain" && <small className="field-hint">邮件标识 {item.message_id}</small>}
            </div>
            <span className={`status ${sendStates[item.state].tone}`}>{sendStates[item.state].label}</span>
          </li>)}
        </ul>
      </article>)}
      {(cursors.length > 0 || records?.length === 25) && <div className="dispatch-actions">
        <button type="button" className="button" disabled={busy || !cursors.length}
          onClick={() => { setRecords(undefined); setTarget(undefined); setCursors(previous => previous.slice(0, -1)); }}>上一页</button>
        <button type="button" className="button" disabled={busy || records?.length !== 25}
          onClick={() => {
            const next = records?.at(-1)?.id;
            if (next) { setRecords(undefined); setTarget(undefined); setCursors(previous => [...previous, next]); }
          }}>下一页</button>
      </div>}
    </div>
  </Modal>;
}

export function DeliveryDispatchActions({ draft, admin, disabled, onReleaseChange, onReleaseCreated, onFix, onStale }: {
  draft: DeliveryDraft; admin: boolean; disabled: boolean; onReleaseChange: (release: Release | null) => void;
  onReleaseCreated: () => void;
  onFix: (mode: "review" | "shipment") => void; onStale: () => void;
}) {
  const [releases, setReleases] = useState<Release[]>();
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const [panel, setPanel] = useState<"confirm" | "send" | "releases" | "history">();
  const [selected, setSelected] = useState<Release>();
  const [newlyQueued, setNewlyQueued] = useState(false);
  const latest = releases?.[0];
  const current = latest?.draft_revision === draft.revision;
  const close = () => setPanel(undefined);

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<Release[]>(`/delivery/${draft.id}/releases`, { signal: controller.signal })
      .then(values => {
        if (controller.signal.aborted) return;
        setReleases(values); onReleaseChange(values[0] ?? null); setError("");
      })
      .catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [draft.id, draft.revision, reload, onReleaseChange]);

  function history(release: Release, queued = false) {
    setSelected(release); setNewlyQueued(queued); setPanel("history");
  }

  return <>
    {error && <button type="button" className="button" title={error} onClick={() => setReload(value => value + 1)}>重读确认版本</button>}
    {latest && <button type="button" className="button" disabled={disabled} onClick={() => setPanel("releases")}>
      <HistoryOutlined aria-hidden="true" /> 确认版本
    </button>}
    {latest && current && <a className="button" href={releasedFile(latest)}>
      <DownloadOutlined aria-hidden="true" /> 下载确认版 V{latest.version}
    </a>}
    {admin && (current && latest
      ? <button type="button" className="button primary-button" disabled={disabled}
        onClick={() => { setSelected(latest); setPanel("send"); }}><SendOutlined aria-hidden="true" /> 邮件发送</button>
      : <button type="button" className="button primary-button" disabled={disabled || !releases}
        onClick={() => setPanel("confirm")}><CheckCircleOutlined aria-hidden="true" /> 确认交付</button>)}
    {panel === "confirm" && <ConfirmDelivery draft={draft} onClose={close} onStale={onStale}
      onAlreadyConfirmed={() => { setReload(value => value + 1); setPanel("releases"); }}
      onFix={mode => { close(); onFix(mode); }} onConfirmed={release => {
        setReleases(previous => [release, ...(previous ?? []).filter(value => value.id !== release.id)]);
        onReleaseChange(release); onReleaseCreated(); setPanel("releases");
      }} />}
    {panel === "releases" && <Modal open centered width={660} title="确认版本" onCancel={close}
      footer={<button type="button" className="button" onClick={close}>关闭</button>}>
      <ul className="dispatch-stack-list release-list">
        {releases?.map((release, index) => <li key={release.id}>
          <div>
            <strong>V{release.version}</strong>{" "}
            <span className={`status ${index === 0 ? "complete" : "queued"}`}>{index === 0 ? "最新确认版" : "历史版本"}</span>
            <p>{release.filename}</p>
            <p className="field-hint">{release.confirmed_by} 确认于 {chinaTime.format(new Date(release.confirmed_at))}</p>
            <p className="field-hint">草稿版本 {release.draft_revision} · 规则 {release.rule_version}
              {release.ship_through && ` · 核验截至 ${release.ship_through}`}
              {release.retained_issues > 0 && ` · 保留 ${release.retained_issues} 项待核对记录`}
            </p>
          </div>
          <div className="dispatch-actions">
            <a className="button" href={releasedFile(release)}>下载</a>
            {admin && <button type="button" className="button" onClick={() => history(release)}>发送记录</button>}
            {admin && index === 0 && <button type="button" className="button primary-button"
              onClick={() => { setSelected(release); setPanel("send"); }}>邮件发送</button>}
          </div>
        </li>)}
      </ul>
    </Modal>}
    {panel === "send" && selected && <SendDelivery key={selected.id} release={selected}
      onClose={close} onSent={() => history(selected, true)} />}
    {panel === "history" && selected && <SendHistory key={selected.id} release={selected}
      canRetry={selected.id === latest?.id} newlyQueued={newlyQueued} onClose={close} />}
  </>;
}
