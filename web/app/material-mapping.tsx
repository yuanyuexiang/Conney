"use client";

import { DownOutlined, FileExcelOutlined, PlusOutlined, SearchOutlined, TagsOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useId, useState } from "react";
import {
  customerLabels, type Customer, type MappingValues, type MaterialCandidate, type MaterialMapping,
} from "./delivery-api";
import { messageOf, relayRequest } from "./relay-api";
import { WorkbenchSelect } from "./workbench-select";

const customerOptions = (Object.entries(customerLabels) as [Customer, string][]).map(
  ([value, label]) => ({ value, label }),
);
const customerFilters = [{ value: "" as const, label: "全部客户" }, ...customerOptions];
const primaryFields = [
  ["order_no", "采购订单"],
  ["customer_item", "客户料号"],
  ["internal_item", "康尼料号"],
] as const;
const detailFields = [
  ["order_line", "订单行"],
  ["ship_to", "收货方"],
  ["supplier_item", "供应商料号"],
  ["drawing", "图号"],
] as const;
const quantityFields = [
  ["minimum_pack", "最小包装量"],
  ["units_per_carton", "每箱数量"],
  ["cartons_per_pallet", "每托箱数"],
  ["safety_stock", "安全库存"],
] as const;

function FieldName({ label, required }: { label: string; required?: boolean }) {
  return <span className="mapping-field-name">
    {label}{required && <span className="mapping-required" aria-hidden="true">*</span>}
  </span>;
}

export function MappingDialog({ initial, existing, source, onClose, onSaved }: {
  initial: Partial<MappingValues>;
  existing?: MaterialMapping;
  source?: MaterialCandidate;
  onClose: () => void;
  onSaved: (mapping: MaterialMapping) => void;
}) {
  const [customer, setCustomer] = useState<Customer>(initial.customer ?? "drx");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const formId = useId();
  const matchHeading = useId();
  const hasDetails = detailFields.some(([key]) => initial[key]);
  const hasPackaging = quantityFields.some(([key]) => initial[key] != null);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const identifiers = Object.fromEntries([...primaryFields, ...detailFields].map(
      ([key]) => [key, String(form.get(key) ?? "").trim() || null],
    ));
    const quantities = Object.fromEntries(quantityFields.map(([key]) => [key, form.get(key) ? Number(form.get(key)) : null]));
    try {
      const mapping = await relayRequest<MaterialMapping>(existing ? `/material-mappings/${existing.id}` : "/material-mappings", {
        method: existing ? "PUT" : "POST",
        body: JSON.stringify({ customer, ...identifiers, ...quantities, ...(existing ? { revision: existing.revision } : {}) }),
      });
      onSaved(mapping);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setSaving(false);
    }
  }

  function identifierInput([key, label]: typeof primaryFields[number] | typeof detailFields[number]) {
    const required = key === "customer_item" || key === "internal_item" ||
      (key === "order_no" && customer === "drx");
    return <label key={key}>
      <FieldName label={label} required={required} />
      <input name={key} defaultValue={initial[key] ?? ""} maxLength={80} required={required} />
    </label>;
  }

  return (
    <Modal open title={<div className="mapping-modal-title">
      <span className="mapping-modal-icon"><TagsOutlined aria-hidden="true" /></span>
      <span>{existing ? "编辑物料匹配" : source ? "补录物料匹配" : "新增物料匹配"}</span>
    </div>} width={688} centered
      classNames={{
        container: "mapping-modal-panel", header: "mapping-modal-header", body: "mapping-modal-body",
        footer: "mapping-modal-footer", close: "mapping-modal-close",
      }}
      styles={{ container: { padding: 0 }, header: { margin: 0 }, footer: { margin: 0 } }}
      closable={saving ? false : { "aria-label": "关闭物料匹配" }} keyboard={!saving}
      mask={{ closable: false }} onCancel={() => { if (!saving) onClose(); }}
      footer={<>
        {error && <p className="notice failed" role="alert">{error}</p>}
        <div className="mapping-modal-actions">
          <button type="button" className="button" onClick={onClose} disabled={saving}>取消</button>
          <button type="submit" form={formId} className="button primary-button" disabled={saving}>
            {saving ? "正在保存…" : "保存映射"}
          </button>
        </div>
      </>}>
      <form id={formId} className="mapping-form" onSubmit={(event) => void save(event)}
        onInvalidCapture={(event) => {
          const group = (event.target as HTMLElement).closest("details");
          if (group) group.open = true;
        }}>
        {source && <div className="mapping-source-note">
          <FileExcelOutlined aria-hidden="true" />
          <span>{source.source_filename}<small>{source.source_cell} · {source.source_date}</small></span>
        </div>}
        <fieldset disabled={saving} aria-labelledby={matchHeading}>
          <div className="mapping-form-heading">
            <h3 id={matchHeading}>基本匹配</h3>
            <span><span className="mapping-required" aria-hidden="true">*</span> 必填</span>
          </div>
          <div className="mapping-field-grid">
            <label>
              <FieldName label="客户" required />
              <WorkbenchSelect label="客户" aria-required="true" value={customer} options={customerOptions}
                onChange={setCustomer} disabled={saving} />
            </label>
            {primaryFields.map(identifierInput)}
          </div>
        </fieldset>
        <fieldset disabled={saving} className="mapping-form-options">
          <details open={hasDetails}>
            <summary><DownOutlined aria-hidden="true" /><span>订单与物料补充信息</span><small>选填</small></summary>
            <div className="mapping-field-grid">{detailFields.map(identifierInput)}</div>
          </details>
          <details open={hasPackaging}>
            <summary><DownOutlined aria-hidden="true" /><span>包装与库存</span><small>选填</small></summary>
            <div className="mapping-field-grid">
              {quantityFields.map(([key, label]) => (
                <label key={key}>
                  <FieldName label={label} />
                  <input name={key} type="number" inputMode="numeric" step={1} min={key === "safety_stock" ? 0 : 1}
                    max={1000000000} defaultValue={initial[key] ?? ""} />
                </label>
              ))}
            </div>
          </details>
        </fieldset>
      </form>
    </Modal>
  );
}

export function MaterialMappingPage() {
  type MappingRow = MaterialMapping | MaterialCandidate;
  const [result, setResult] = useState<{ key: string; rows: MappingRow[]; hasMore: boolean }>();
  const [view, setView] = useState<"pending" | "matched">("pending");
  const [q, setQ] = useState("");
  const [customer, setCustomer] = useState<Customer | "">("");
  const [revision, setRevision] = useState(0);
  const [failure, setFailure] = useState<{ key: string; message: string }>();
  const [saved, setSaved] = useState("");
  const [editing, setEditing] = useState<MappingRow | "new">();
  const [loadingPage, setLoadingPage] = useState<string>();
  const [query, setQuery] = useState("");
  const params = new URLSearchParams({ q: query, ...(customer ? { customer } : {}) }).toString();
  const pending = view === "pending";
  const endpoint = pending ? "/material-mappings/candidates" : "/material-mappings";
  const requestUrl = `${endpoint}?${params}`;
  const key = `${requestUrl}#${revision}`;
  const rows = result?.key === key ? result.rows : undefined;
  const hasMore = result?.key === key && result.hasMore;
  const loadingMore = loadingPage === key;
  const error = failure?.key === key ? failure.message : "";

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<MappingRow[]>(requestUrl, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        setResult({ key, rows: result, hasMore: result.length === 100 });
        setFailure(undefined);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setFailure({ key, message: messageOf(reason) });
      });
    return () => controller.abort();
  }, [key, requestUrl]);

  async function more() {
    const before = rows?.at(-1)?.id;
    if (!before || loadingMore) return;
    setLoadingPage(key);
    try {
      const next = await relayRequest<MappingRow[]>(`${requestUrl}&before=${before}`);
      setResult((current) => current?.key === key && current.rows.at(-1)?.id === before
        ? { key, rows: [...current.rows, ...next], hasMore: next.length === 100 }
        : current);
      setFailure((current) => current?.key === key ? undefined : current);
    } catch (reason) {
      setFailure((current) => current && current.key !== key ? current : { key, message: messageOf(reason) });
    } finally {
      setLoadingPage((current) => current === key ? undefined : current);
    }
  }

  return (
    <section className="page-card mapping-card" aria-label="物料匹配列表">
      <div className="mapping-list-heading">
        <div className="delivery-view-tabs" role="group" aria-label="物料匹配状态">
          <button type="button" aria-pressed={pending} onClick={() => setView("pending")}>待匹配</button>
          <button type="button" aria-pressed={!pending} onClick={() => setView("matched")}>已匹配</button>
        </div>
        {rows && <span className="mapping-list-count">{hasMore ? "已加载 " : ""}{rows.length} 条</span>}
      </div>
      <div className="delivery-toolbar">
        <form className="delivery-filters" onSubmit={(event) => { event.preventDefault(); setQuery(q.trim()); }}>
          <label className="sr-only" htmlFor="mapping-search">搜索料号或订单</label>
          <input id="mapping-search" value={q} onChange={(event) => setQ(event.target.value)} maxLength={120}
            placeholder="搜索料号或订单" />
          <button className="button" type="submit" aria-label="搜索"><SearchOutlined aria-hidden="true" /></button>
          <WorkbenchSelect label="按客户筛选" filter active={customer !== ""} value={customer}
            options={customerFilters} onChange={setCustomer} />
        </form>
        <button type="button" className="button primary-button" onClick={() => { setSaved(""); setEditing("new"); }}>
          <PlusOutlined aria-hidden="true" /> 新增映射
        </button>
      </div>
      {saved && <p className="notice" role="status">{saved}</p>}
      {error && <p className="notice failed" role="alert">{error}</p>}
      {rows && rows.length > 0 ? (
        <div className="table-scroll">
          <table><thead><tr>
            {["客户", "客户料号", pending ? "来源表格" : "康尼料号", "采购订单", "订单行 / 收货方", "供应商料号", "操作"]
              .map((label) => <th scope="col" key={label}>{label}</th>)}
          </tr></thead><tbody>{rows.map((row) => <tr key={row.id}>
            <td>{customerLabels[row.customer]}</td><td><strong>{row.customer_item}</strong></td>
            <td>{"internal_item" in row ? <strong>{row.internal_item}</strong> :
              <span className="mapping-source-file" title={row.source_filename}>{row.source_filename}<small>{row.source_cell}</small></span>}</td>
            <td>{row.order_no || "—"}</td><td>{row.order_line || "—"} / {row.ship_to || "—"}</td><td>{row.supplier_item || "—"}</td>
            <td><button type="button" className="text-button mapping-row-action" onClick={() => { setSaved(""); setEditing(row); }}>
              {pending ? "补录康尼料号" : "编辑"}
            </button></td>
          </tr>)}</tbody></table>
        </div>
      ) : !error && <p className="empty" role="status">
        {rows ? (pending ? "没有符合条件的待匹配物料" : "没有符合条件的已匹配物料") : "正在读取物料记录…"}
      </p>}
      {hasMore && <button type="button" className="button" disabled={loadingMore} onClick={() => void more()}>加载更多</button>}
      {editing && <MappingDialog initial={editing === "new" ? { customer: customer || "drx" } : editing}
        existing={editing !== "new" && "internal_item" in editing ? editing : undefined}
        source={editing !== "new" && "source_filename" in editing ? editing : undefined}
        onClose={() => setEditing(undefined)}
        onSaved={(mapping) => {
          if (editing === "new") setView("matched");
          setRevision((value) => value + 1);
          setSaved(`已保存 ${mapping.customer_item} → ${mapping.internal_item}`);
          setEditing(undefined);
        }} />}
    </section>
  );
}
