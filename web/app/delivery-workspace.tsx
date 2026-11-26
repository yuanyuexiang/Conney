"use client";

import {
  ArrowLeftOutlined, DeleteOutlined, DownloadOutlined, FileExcelOutlined, ReloadOutlined,
} from "@ant-design/icons";
import { Drawer, Modal, type RefSelectProps } from "antd";
import { type FormEvent, useEffect, useId, useMemo, useRef, useState } from "react";
import {
  addQuantities, entryLabels, mappingLabels, needsReview, reviewLabels, shortDate,
  type DeliveryDraft, type Entry, type Material, type ReviewAction, type ReviewInput, type ReviewKind,
  type Shipment, type Summary,
} from "./delivery-api";
import { MappingDialog } from "./material-mapping";
import { messageOf, relayRequest, RelayRequestError } from "./relay-api";
import { WorkbenchHeader, type WorkbenchHeading } from "./workbench-header";
import { WorkbenchSelect } from "./workbench-select";
import { chinaTime } from "./workbench-status";

type GridMode = "demand" | "shipment";
type Mode = GridMode | "review";
type Selection = { materialId: number; entryIds: number[]; label: string };

const shipmentModes: { value: Shipment["mode"]; label: string }[] = [
  { value: "air", label: "空运 · 14 天" },
  { value: "sea", label: "海运 · 84 天" },
];

function periodKey(entry: Entry, mode: GridMode) {
  if (mode === "demand") return entry.period;
  const shipment = entry.shipment;
  return shipment ? `${shipment.ship_date}|${shipment.mode}|${shipment.manual_eta ?? ""}` : undefined;
}

function periodLabel(key: string, mode: GridMode) {
  if (mode === "demand") return key;
  const [day, transport] = key.split("|");
  return `${day} · ${transport === "air" ? "空运" : "海运"}`;
}

function DeliveryGrid({ draft, mode, onSelect }: {
  draft: DeliveryDraft;
  mode: GridMode;
  onSelect: (selection: Selection) => void;
}) {
  const [datePage, setDatePage] = useState(0);
  const data = useMemo(() => {
    const axis = new Map<string, Entry>();
    const rows = draft.materials.map((material) => {
      const cells = new Map<string, Entry[]>();
      for (const entry of material.entries) {
        if (mode === "shipment" && material.mapping_state !== "matched") continue;
        const key = periodKey(entry, mode);
        if (key === undefined) continue;
        axis.set(key, entry);
        const cell = cells.get(key);
        if (cell) cell.push(entry);
        else cells.set(key, [entry]);
      }
      return { material, cells };
    });
    return {
      axis: [...axis].sort(([left], [right]) => left.localeCompare(right)),
      rows: mode === "shipment" ? rows.filter(({ cells }) => cells.size > 0) : rows,
    };
  }, [draft, mode]);
  const datesPerPage = 52;
  const datePages = Math.max(1, Math.ceil(data.axis.length / datesPerPage));
  const page = Math.min(datePages - 1, datePage);
  const axis = data.axis.slice(page * datesPerPage, (page + 1) * datesPerPage);

  if (!data.axis.length && mode === "shipment") return (
    <div className="delivery-empty">
      <FileExcelOutlined aria-hidden="true" />
      <h3>还没有发货记录</h3>
      <p>在客户需求里点开一笔已匹配的日期需求，填写有依据的发货日期和运输方式。</p>
    </div>
  );

  return (<>
    {datePages > 1 && <div className="delivery-date-pages" aria-label="日期列分页">
      <button className="button" type="button" disabled={page === 0} onClick={() => setDatePage(page - 1)}>上一段</button>
      <span>{periodLabel(axis[0][0], mode)} 至 {periodLabel(axis.at(-1)![0], mode)}</span>
      <button className="button" type="button" disabled={page + 1 === datePages} onClick={() => setDatePage(page + 1)}>下一段</button>
    </div>}
    <div className="delivery-grid table-scroll" tabIndex={0} aria-label={mode === "demand" ? "客户需求表" : "发货计划表"}>
      <table>
        <thead><tr>
          <th scope="col" className="freeze kn-column">康尼料号</th>
          <th scope="col" className="freeze customer-column">客户料号 / 订单</th>
          {axis.map(([key, entry]) => (
            <th scope="col" key={key}>
              {periodLabel(key, mode)}
              {mode === "shipment" && entry.eta && <small>ETA {entry.eta}</small>}
            </th>
          ))}
        </tr></thead>
        <tbody>{data.rows.map(({ material, cells }) => (
          <tr key={material.id}>
            <th scope="row" className="freeze kn-column">
              {material.mapping_state === "matched" ? material.mapping?.internal_item :
                <span className="delivery-badge review">{mappingLabels[material.mapping_state]}</span>}
            </th>
            <td className="freeze customer-column"><strong>{material.customer_item ?? "—"}</strong><small>{material.order_no ?? "—"}</small></td>
            {axis.map(([key]) => {
              const entries = cells.get(key) ?? [];
              const countable = entries.filter(({ state }) => state !== "code_review" && state !== "excluded");
              const codePending = entries.some(({ state }) => state === "code_review");
              const reviewLabel = codePending ? "代码待确认" : "已排除";
              return (
                <td className="quantity-cell" key={key}>
                  {entries.length > 0 && <button className={countable.length ? "quantity-link" : "quantity-link needs-review"}
                    type="button" onClick={() => onSelect({ materialId: material.id,
                      entryIds: entries.map(({ id }) => id), label: periodLabel(key, mode) })}>
                    {countable.length ? addQuantities(countable) : <>
                      <span>{entries.length === 1 ? `原报文 ${addQuantities(entries)}` : `${entries.length} 条原报文`}</span>
                      <small className={codePending ? "quantity-review-label" : undefined}>{reviewLabel}</small>
                    </>}
                    {countable.length > 0 && countable.length !== entries.length &&
                      <span className="review-dot" aria-label={`另有${reviewLabel}的记录`} title={`另有${reviewLabel}的记录`} />}
                  </button>}
                </td>
              );
            })}
          </tr>
        ))}</tbody>
      </table>
    </div>
  </>);
}

function ShipmentForm({ entry, busy, error, onSave, onClose }: {
  entry: Entry;
  busy: boolean;
  error: string;
  onSave: (shipment: object | null) => void;
  onClose: () => void;
}) {
  const [mode, setMode] = useState(entry.shipment?.mode);
  const [missingMode, setMissingMode] = useState(false);
  const modeRef = useRef<RefSelectProps>(null);
  const modeErrorId = useId();

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!mode) {
      setMissingMode(true);
      modeRef.current?.focus();
      return;
    }
    const form = new FormData(event.currentTarget);
    onSave({
      ship_date: form.get("ship_date"),
      mode,
      manual_eta: form.get("manual_eta") || null,
      source_note: form.get("source_note"),
    });
  }
  return (
    <form className="account-form shipment-form" onSubmit={submit}>
      <label>发货日期<input type="date" name="ship_date" required defaultValue={entry.shipment?.ship_date} /></label>
      <label>运输方式
        <WorkbenchSelect ref={modeRef} label="运输方式" aria-required="true"
          aria-invalid={missingMode} aria-describedby={missingMode ? modeErrorId : undefined}
          status={missingMode ? "error" : undefined} disabled={busy} value={mode}
          options={shipmentModes} placeholder="请选择运输方式" onChange={(value) => {
            setMode(value);
            setMissingMode(false);
          }} />
        {missingMode && <span className="shipment-field-error" id={modeErrorId} role="alert">请选择运输方式。</span>}
      </label>
      <label>人工 ETA（可选）<input type="date" name="manual_eta" defaultValue={entry.shipment?.manual_eta ?? ""} /></label>
      <label>发货依据<input name="source_note" maxLength={240} defaultValue={entry.shipment?.source_note}
        placeholder="例如运输计划编号或确认邮件" required /></label>
      {error && <p className="notice failed" role="alert">{error}</p>}
      <div className="account-form-actions">
        {entry.shipment && <button className="button" type="button" disabled={busy} onClick={() => onSave(null)}>清除发货信息</button>}
        <button type="button" className="button" disabled={busy} onClick={onClose}>取消</button>
        <button type="submit" className="button primary-button" disabled={busy}>{busy ? "正在保存…" : "保存发货信息"}</button>
      </div>
    </form>
  );
}

function EntryReviewForm({ entry, kind, busy, error, onSave, onClose }: {
  entry: Entry;
  kind: ReviewKind;
  busy: boolean;
  error: string;
  onSave: (review: ReviewInput) => void;
  onClose: () => void;
}) {
  const [action, setAction] = useState(entry.review?.action);
  const actions: ReviewAction[] = kind === "code_review"
    ? (entry.unit === "C62" ? ["include", "exclude"] : ["exclude"])
    : (entry.earliest_date && entry.latest_date ? ["keep_interval", "set_date"] : ["set_date"]);
  const needsDate = action === "set_date" || (action === "include" && !entry.requested_date);
  const options = actions.map((value) => ({ value, label: reviewLabels[value] }));
  return (
    <form className="account-form shipment-form" aria-label="核对结论"
      onSubmit={(event) => {
        event.preventDefault();
        if (!action || busy) return;
        const fields = new FormData(event.currentTarget);
        onSave({ action, delivery_date: needsDate ? String(fields.get("delivery_date")) : null,
          source_note: String(fields.get("source_note") ?? "").trim() });
      }}>
      <label>核对结论
        <WorkbenchSelect label="核对结论" aria-required="true" disabled={busy} value={action}
          options={options} placeholder="请选择核对结论" onChange={setAction} />
      </label>
      {needsDate && <label>确认交货日
        <input type="date" name="delivery_date" required disabled={busy} defaultValue={entry.review?.delivery_date ?? ""}
          min={entry.earliest_date ?? undefined} max={entry.latest_date ?? undefined} />
      </label>}
      <label>核对依据
        <input name="source_note" maxLength={240} required disabled={busy} defaultValue={entry.review?.source_note}
          placeholder="例如客户确认邮件或计划编号" />
      </label>
      {error && <p className="notice failed" role="alert">{error}</p>}
      <div className="account-form-actions">
        <button type="button" className="button" disabled={busy} onClick={onClose}>取消</button>
        <button type="submit" className="button primary-button" disabled={busy || !action}>
          {busy ? "正在保存…" : "保存核对结论"}
        </button>
      </div>
    </form>
  );
}

function EntryReviewDetails({ entry }: { entry: Entry }) {
  const saved = entry.review;
  if (!saved) return null;
  return (
    <dl className="delivery-review-result">
      <div><dt>核对结论</dt><dd>{reviewLabels[saved.action]}</dd></div>
      {saved.delivery_date && <div><dt>确认交货日</dt><dd>{saved.delivery_date}</dd></div>}
      <div><dt>核对依据</dt><dd>{saved.source_note}</dd></div>
      <div><dt>核对人 / 时间</dt><dd>{saved.reviewed_by} · {chinaTime.format(new Date(saved.reviewed_at))}</dd></div>
    </dl>
  );
}

function DeliveryDetail({ id, admin, review, onChange, onDeleted, onBack, onArchive }: {
  id: number;
  admin: boolean;
  review: boolean;
  onChange: (draft: DeliveryDraft) => void;
  onDeleted: (draft: DeliveryDraft) => void;
  onBack: () => void;
  onArchive: (mailId: number) => void;
}) {
  const [detail, setDetail] = useState<DeliveryDraft>();
  const [mode, setMode] = useState<Mode>(review ? "review" : "demand");
  const [error, setError] = useState("");
  const [selection, setSelection] = useState<Selection>();
  const [mappingMaterial, setMappingMaterial] = useState<Material>();
  const [shipmentId, setShipmentId] = useState<number>();
  const [shipmentError, setShipmentError] = useState("");
  const [reviewId, setReviewId] = useState<number>();
  const [reviewError, setReviewError] = useState("");
  const [busy, setBusy] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [deleteRevision, setDeleteRevision] = useState<number>();
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<DeliveryDraft>(`/delivery/${id}`, { signal: controller.signal })
      .then((draft) => { if (!controller.signal.aborted) { setDetail(draft); setError(""); } })
      .catch((reason) => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [id, reload]);

  async function update(path: string, method: string, values: object, operation?: "shipment" | "review") {
    if (!detail || busy) return;
    setBusy(true);
    const setOperationError = operation === "review" ? setReviewError : operation === "shipment" ? setShipmentError : setError;
    setOperationError("");
    try {
      const draft = await relayRequest<DeliveryDraft>(`/delivery/${id}/${path}`, {
        method, body: JSON.stringify({ ...values, revision: detail.revision }),
      });
      setDetail(draft);
      onChange(draft);
      if (operation === "shipment") setShipmentId(undefined);
      if (operation === "review") setReviewId(undefined);
    } catch (reason) {
      setOperationError(messageOf(reason));
      if (reason instanceof RelayRequestError && reason.status === 409) setReload((value) => value + 1);
    } finally {
      setBusy(false);
    }
  }

  async function download() {
    if (!detail) return;
    setError("");
    setDownloading(true);
    try {
      const response = await fetch(`/api/v1/delivery/${id}/draft.xlsx?revision=${detail.revision}`, { cache: "no-store" });
      if (response.status === 401) window.dispatchEvent(new Event("relay-session-expired"));
      if (!response.ok) {
        const result = await response.json().catch(() => ({})) as { detail?: string };
        throw new RelayRequestError(response.status, result.detail ?? "");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `Relay-DRX-${id}-草稿.xlsx`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      setError(messageOf(reason));
      if (reason instanceof RelayRequestError && reason.status === 409) setReload((value) => value + 1);
    } finally {
      setDownloading(false);
    }
  }

  async function removeDraft() {
    if (!detail || deleteRevision !== detail.revision || deleting) return;
    setDeleting(true);
    setDeleteError("");
    try {
      await relayRequest<void>(`/delivery/${id}?revision=${deleteRevision}`, { method: "DELETE" });
      onDeleted(detail);
    } catch (reason) {
      if (reason instanceof RelayRequestError && reason.status === 404) {
        onDeleted(detail);
        return;
      }
      setDeleteError(messageOf(reason));
      if (reason instanceof RelayRequestError && reason.status === 409) setReload((value) => value + 1);
    } finally {
      setDeleting(false);
    }
  }

  function show(selection: Selection) {
    setShipmentId(undefined);
    setShipmentError("");
    setReviewId(undefined);
    setReviewError("");
    setSelection(selection);
  }

  const material = detail?.materials.find((item) => item.id === selection?.materialId);
  const entries = material?.entries.filter((entry) => selection?.entryIds.includes(entry.id)) ?? [];
  const reviewCounts = new Map<Entry["state"], number>();
  for (const row of detail?.materials ?? []) {
    for (const entry of row.entries) {
      if (needsReview(entry)) reviewCounts.set(entry.state, (reviewCounts.get(entry.state) ?? 0) + 1);
    }
  }
  const reviewBreakdown = [
    { label: "物料映射待核对", count: detail?.pending_mappings ?? 0, unit: "个" },
    { label: "报文代码待确认", count: reviewCounts.get("code_review") ?? 0, unit: "条" },
    { label: "日期区间待核对", count: reviewCounts.get("period_review") ?? 0, unit: "条" },
    { label: "待补发货安排", count: reviewCounts.get("transport_missing") ?? 0, unit: "条" },
  ].filter(({ count }) => count > 0);

  return (
    <section className="delivery-detail" aria-label="交付批次详情">
      <header className="delivery-detail-header">
        <button className="back-button" type="button" onClick={onBack}><ArrowLeftOutlined /> 返回批次</button>
        {detail ? (
          <>
            <div className="delivery-detail-title">
              <div>
                <p className="delivery-eyebrow">德科 DRX · 报文 {detail.document_no ?? "—"}</p>
                <h2>交付草稿 <span className="delivery-badge review">版本 {detail.revision}</span></h2>
              </div>
              <div className="delivery-detail-actions">
                {admin && <button type="button" className="button delivery-delete-button" aria-haspopup="dialog"
                  disabled={downloading || busy || deleting} onClick={() => {
                    setDeleteError("");
                    setDeleteRevision(detail.revision);
                  }}><DeleteOutlined aria-hidden="true" /> 删除草稿</button>}
                <button type="button" className="button primary-button" disabled={downloading || busy} onClick={() => void download()}>
                  <DownloadOutlined aria-hidden="true" /> {downloading ? "正在生成…" : "下载草稿"}
                </button>
              </div>
            </div>
            <div className="delivery-meta">
              <button type="button" className="text-button" onClick={() => onArchive(detail.mail_id)} title={detail.filename}>
                <FileExcelOutlined aria-hidden="true" /> {detail.filename}
              </button>
              <span>{shortDate(detail.document_date)}</span>
            </div>
          </>
        ) : <h2>交付草稿</h2>}
      </header>
      {error && <p className="notice failed" role="alert">{error}</p>}
      {!detail && !error && <p className="empty" role="status">正在读取交付草稿…</p>}
      {detail && (
        <>
          <div className="delivery-view-bar">
            <div className="delivery-view-tabs" aria-label="计划视图">
              {([["demand", "客户需求"], ["shipment", "发货计划"], ["review", "待核对"]] as const).map(([key, label]) => (
                <button type="button" key={key} aria-pressed={mode === key} onClick={() => setMode(key)}>
                  {label}{key === "review" && detail.pending_mappings + detail.pending_entries > 0 &&
                    <span>{detail.pending_mappings + detail.pending_entries}</span>}
                </button>
              ))}
            </div>
            {admin && <button type="button" className="button" disabled={busy} onClick={() => void update("refresh", "POST", {})}>
              <ReloadOutlined aria-hidden="true" /> 重新匹配
            </button>}
          </div>
          {mode === "review" ? (
            <div className="delivery-review-list">
              {reviewBreakdown.length > 0 && <dl className="delivery-review-counts" aria-label="待核对原因">
                {reviewBreakdown.map(({ label, count, unit }) => <div key={label}>
                  <dt>{label}</dt><dd>{count} {unit}</dd>
                </div>)}
              </dl>}
              {detail.materials.filter((row) => row.mapping_state !== "matched" || row.entries.some(needsReview)).map((row) => {
                const pending = row.entries.filter(needsReview);
                return (
                  <div className="delivery-review-row" key={row.id}>
                    <div><strong>{row.customer_item ?? "缺少客户料号"}</strong><small>订单 {row.order_no ?? "—"}{pending.length > 0 && ` · ${pending.length} 条需求待处理`}</small></div>
                    <span className={`delivery-badge ${row.mapping_state === "matched" ? "" : "review"}`}>{mappingLabels[row.mapping_state]}</span>
                    {admin && row.mapping_state === "missing" && row.customer_item && row.order_no &&
                      <button type="button" className="button" disabled={busy} onClick={() => setMappingMaterial(row)}>补充映射</button>}
                    {admin && row.mapping_state === "supplier_mismatch" && row.mapping &&
                      <button type="button" className="button" disabled={busy} onClick={() => setMappingMaterial(row)}>核对映射</button>}
                    <button type="button" className="text-button" onClick={() => show({
                      materialId: row.id, entryIds: pending.map(({ id: entryId }) => entryId), label: "待核对记录",
                    })}>处理记录</button>
                  </div>
                );
              })}
              {detail.pending_mappings + detail.pending_entries === 0 && <p className="empty">该批次没有待核对记录</p>}
            </div>
          ) : (
            <DeliveryGrid key={mode} draft={detail} mode={mode} onSelect={show} />
          )}
        </>
      )}
      <Drawer open={selection !== undefined} title="数量与来源" size={540} onClose={() => setSelection(undefined)}>
        {material && <>
          <div className="delivery-source-summary">
            <h3>{material.customer_item ?? "未知物料"} <span className="delivery-badge">{mappingLabels[material.mapping_state]}</span></h3>
            <p>订单 {material.order_no ?? "—"} · {selection?.label}</p>
            {material.mapping_state === "matched" && material.mapping &&
              <p>康尼料号 {material.mapping.internal_item} · 映射 #{material.mapping.id} · 版本 {material.mapping.revision}</p>}
            {material.customer_item_cell && <p className="source-coordinate">{material.customer_item_cell}</p>}
            {admin && material.mapping_state === "missing" && material.customer_item && material.order_no &&
              <button type="button" className="button" disabled={busy} onClick={() => setMappingMaterial(material)}>补充映射</button>}
          </div>
          {entries.map((entry) => {
            const kind = entry.review?.kind ??
              (entry.state === "code_review" || entry.state === "period_review" ? entry.state : undefined);
            return (
              <article className="delivery-source-row" key={entry.id}>
                <div className="delivery-source-title">
                  <strong>{addQuantities([entry])} <small>{entry.unit ?? ""}</small></strong>
                  <span className={`delivery-badge ${needsReview(entry) ? "review" : ""}`}>{entryLabels[entry.state]}</span>
                </div>
                <dl>
                  <div><dt>附件原值</dt><dd>{entry.raw_quantity ?? entry.quantity}</dd></div>
                  <div><dt>原始日期</dt><dd>{entry.requested_date ?? (entry.earliest_date || entry.latest_date ? entry.period : "—")}</dd></div>
                  <div><dt>数量位置</dt><dd className="source-coordinate">{entry.quantity_cell ?? "—"}</dd></div>
                  <div><dt>日期位置</dt><dd className="source-coordinate">{[entry.date_cell, entry.end_date_cell].filter(Boolean).join(" / ") || "—"}</dd></div>
                  <div><dt>数量类型 / 状态</dt><dd>{entry.code ?? "—"} / {entry.status_code ?? "—"}</dd></div>
                  {entry.shipment && <>
                    <div><dt>发货日 / 运输</dt><dd>{entry.shipment.ship_date} · {entry.shipment.mode === "air" ? "空运" : "海运"}</dd></div>
                    <div><dt>ETA</dt><dd>{entry.eta}（{entry.shipment.manual_eta ? "人工填报" : entry.shipment.mode === "air" ? "发货日 + 14 天" : "发货日 + 84 天"}）</dd></div>
                    <div><dt>发货依据</dt><dd>{entry.shipment.source_note} · {entry.shipment.entered_by}</dd></div>
                  </>}
                </dl>
                <EntryReviewDetails entry={entry} />
                {admin && kind && !entry.shipment && (reviewId === entry.id
                  ? <EntryReviewForm entry={entry} kind={kind} busy={busy} error={reviewError}
                    onClose={() => setReviewId(undefined)}
                    onSave={(review) => void update(`reviews/${entry.id}`, "PUT", { review }, "review")} />
                  : <button type="button" className="button" disabled={busy} onClick={() => {
                    setShipmentId(undefined); setReviewError(""); setReviewId(entry.id);
                  }}>{entry.review ? "修改核对结论" : kind === "code_review" ? "确认报文代码" : "核对日期区间"}</button>)}
                {admin && reviewId !== entry.id && material.mapping_state === "matched" && (entry.state === "scheduled" || entry.state === "transport_missing") &&
                  (shipmentId === entry.id ? <ShipmentForm entry={entry} busy={busy} error={shipmentError}
                    onClose={() => setShipmentId(undefined)}
                    onSave={(shipment) => void update(`shipments/${entry.id}`, "PUT", { shipment }, "shipment")} />
                    : <button type="button" className="button" disabled={busy} onClick={() => {
                      setReviewId(undefined); setShipmentError(""); setShipmentId(entry.id);
                    }}>{entry.shipment ? "编辑发货信息" : "填写发货信息"}</button>)}
              </article>
            );
          })}
        </>}
      </Drawer>
      {mappingMaterial && <MappingDialog
        existing={mappingMaterial.mapping ?? undefined}
        initial={mappingMaterial.mapping ?? { customer: "drx", customer_item: mappingMaterial.customer_item ?? "",
          order_no: mappingMaterial.order_no, order_line: mappingMaterial.order_line,
          supplier_item: mappingMaterial.supplier_item }}
        onClose={() => setMappingMaterial(undefined)}
        onSaved={() => { setMappingMaterial(undefined); void update("refresh", "POST", {}); }} />}
      <Modal title="删除交付草稿" open={deleteRevision !== undefined} width={460} centered
        okText="确认删除" cancelText="取消"
        okButtonProps={{ danger: true, disabled: deleteRevision !== detail?.revision }}
        cancelButtonProps={{ autoFocus: true, disabled: deleting }}
        confirmLoading={deleting} keyboard={!deleting}
        closable={deleting ? false : { "aria-label": "关闭删除确认" }} mask={{ closable: false }}
        onCancel={() => { if (!deleting) setDeleteRevision(undefined); }}
        onOk={() => void removeDraft()}>
        <p className="draft-delete-context">
          {detail?.document_no && <strong>报文 {detail.document_no} · </strong>}{detail?.filename}
        </p>
        <p>已录入的核对结论和发货安排会随草稿删除。之后可以从「收件档案」的原始附件重新生成草稿。</p>
        {deleteError && <p className="notice failed draft-delete-error" role="alert">{deleteError}</p>}
      </Modal>
    </section>
  );
}

export function DeliveryWorkspace({ heading, admin, draftId, onSelect, initialReview = false, onArchive }: {
  heading: WorkbenchHeading;
  admin: boolean;
  draftId: number | undefined;
  onSelect: (id: number | undefined) => void;
  initialReview?: boolean;
  onArchive: (mailId: number) => void;
}) {
  const [rows, setRows] = useState<Summary[]>();
  const [status, setStatus] = useState<"all" | "review">(initialReview ? "review" : "all");
  const [search, setSearch] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState("");
  const [more, setMore] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [deletedNotice, setDeletedNotice] = useState("");
  const batchSearch = useRef<HTMLInputElement>(null);
  const moreRequest = useRef<AbortController>(null);
  const forReview = status === "review";
  const query = `/delivery?${new URLSearchParams({ review: String(forReview), q: searchQuery })}`;

  useEffect(() => {
    const timeout = window.setTimeout(() => setSearchQuery(search.trim()), 220);
    return () => window.clearTimeout(timeout);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<Summary[]>(query, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        setRows(result);
        setMore(result.length === 100);
        setError("");
      })
      .catch((reason) => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => { controller.abort(); moreRequest.current?.abort(); };
  }, [query, refresh]);

  async function loadMore() {
    const before = rows?.at(-1)?.id;
    if (!before || loadingMore) return;
    const controller = new AbortController();
    moreRequest.current = controller;
    setLoadingMore(true);
    try {
      const result = await relayRequest<Summary[]>(`${query}&before=${before}`, { signal: controller.signal });
      if (controller.signal.aborted) return;
      setRows((current) => current?.at(-1)?.id === before ? [...current, ...result] : current);
      setMore(result.length === 100);
    } catch (reason) {
      if (!controller.signal.aborted) setError(messageOf(reason));
    } finally {
      moreRequest.current = null;
      setLoadingMore(false);
    }
  }

  function draftDeleted(draft: DeliveryDraft) {
    moreRequest.current?.abort();
    setRows((current) => current?.filter(({ id }) => id !== draft.id));
    setDeletedNotice(`已删除草稿「${draft.document_no ? `报文 ${draft.document_no}` : draft.filename}」。`);
    setRefresh((value) => value + 1);
    onSelect(undefined);
    requestAnimationFrame(() => batchSearch.current?.focus({ preventScroll: true }));
  }

  return (
    <main className={`workbench delivery-workbench ${draftId !== undefined ? "batch-open" : ""}`}>
      <WorkbenchHeader {...heading} />
      {deletedNotice && <p className="notice" role="status">{deletedNotice}</p>}
      <div className="delivery-columns">
        <aside className="delivery-batch-list" aria-label="交付批次">
          <div className="delivery-batch-filters">
            <h2>交付批次</h2>
            <input ref={batchSearch} value={search} aria-label="搜索交付批次" placeholder="搜索报文或附件" maxLength={120}
              onChange={(event) => setSearch(event.target.value)} />
            <WorkbenchSelect label="按核对状态筛选" filter active={forReview} value={status}
              options={[{ value: "all", label: "全部状态" }, { value: "review", label: "待核对" }]}
              onChange={(value) => {
                if (value === status) return;
                setRows(undefined);
                setMore(false);
                setError("");
                setStatus(value);
                onSelect(undefined);
              }} />
          </div>
          {error && <p className="notice failed" role="alert">{error}</p>}
          {rows?.map((row) => {
            const pending = row.pending_mappings + row.pending_entries;
            return (
              <button className={`delivery-batch ${row.id === draftId ? "selected" : ""}`} type="button"
                key={row.id} aria-current={row.id === draftId ? "true" : undefined}
                onClick={() => { setDeletedNotice(""); onSelect(row.id); }}>
                <span><strong>德科 DRX</strong><span className={`delivery-badge ${pending ? "review" : ""}`}>{pending ? "待核对" : "草稿"}</span></span>
                <span>报文 {row.document_no ?? "—"}<time>{shortDate(row.document_date)}</time></span>
                {pending > 0 && <small>{[
                  row.pending_mappings > 0 && `${row.pending_mappings} 个物料待核对`,
                  row.pending_entries > 0 && `${row.pending_entries} 条需求待处理`,
                ].filter(Boolean).join(" · ")}</small>}
              </button>
            );
          })}
          {more && <button className="button batch-more" type="button" disabled={loadingMore} onClick={() => void loadMore()}>加载更多</button>}
          {!error && !rows?.length && <p className="empty">{rows ? "没有符合条件的交付批次" : "正在读取批次…"}</p>}
        </aside>
        {draftId !== undefined ?
          <DeliveryDetail key={draftId} id={draftId} admin={admin} review={forReview} onChange={() => setRefresh((value) => value + 1)}
            onDeleted={draftDeleted} onArchive={onArchive} onBack={() => onSelect(undefined)} />
          : <section className="delivery-empty">
            <FileExcelOutlined aria-hidden="true" />
            <h2>{rows?.length ? "选择交付批次" : "暂无交付草稿"}</h2>
            <p>{rows?.length ? "从左侧打开批次，查看客户需求和核对状态。" : "管理员可以从收件档案中的德科附件生成草稿。"}</p>
          </section>}
      </div>
    </main>
  );
}
