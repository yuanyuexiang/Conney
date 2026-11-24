"use client";

import { DownOutlined, PlusOutlined, SearchOutlined, TagsOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useId, useState } from "react";
import { customerLabels, type Customer, type MappingValues, type MaterialMapping } from "./delivery-api";
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

export function MappingDialog({ initial, existing, onClose, onSaved }: {
  initial: Partial<MappingValues>;
  existing?: MaterialMapping;
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
      <span>{existing ? "编辑物料匹配" : "新增物料匹配"}</span>
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
  const [rows, setRows] = useState<MaterialMapping[]>();
  const [q, setQ] = useState("");
  const [customer, setCustomer] = useState<Customer | "">("");
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [editing, setEditing] = useState<MaterialMapping | "new">();
  const [loadingMore, setLoadingMore] = useState(false);
  const [hasMore, setHasMore] = useState(false);
  const [query, setQuery] = useState("");
  const params = new URLSearchParams({ q: query, ...(customer ? { customer } : {}) }).toString();

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<MaterialMapping[]>(`/material-mappings?${params}`, { signal: controller.signal })
      .then((result) => {
        if (controller.signal.aborted) return;
        setRows(result);
        setHasMore(result.length === 100);
        setError("");
      })
      .catch((reason) => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [params, revision]);

  async function more() {
    const before = rows?.at(-1)?.id;
    if (!before || loadingMore) return;
    setLoadingMore(true);
    try {
      const result = await relayRequest<MaterialMapping[]>(`/material-mappings?${params}&before=${before}`);
      setRows((current) => current?.at(-1)?.id === before ? [...current, ...result] : current);
      setHasMore(result.length === 100);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="page-card mapping-card" aria-label="物料匹配列表">
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
            {["客户", "客户料号", "康尼料号", "订单", "订单行 / 收货方", "供应商料号", "操作"].map((label) => <th scope="col" key={label}>{label}</th>)}
          </tr></thead><tbody>{rows.map((row) => <tr key={row.id}>
            <td>{customerLabels[row.customer]}</td><td>{row.customer_item}</td><td><strong>{row.internal_item}</strong></td>
            <td>{row.order_no || "—"}</td><td>{row.order_line || "—"} / {row.ship_to || "—"}</td><td>{row.supplier_item || "—"}</td>
            <td><button type="button" className="text-button" onClick={() => { setSaved(""); setEditing(row); }}>编辑</button></td>
          </tr>)}</tbody></table>
        </div>
      ) : !error && <p className="empty" role="status">{rows ? "没有匹配的物料记录" : "正在读取物料映射…"}</p>}
      {hasMore && <button type="button" className="button" disabled={loadingMore} onClick={() => void more()}>加载更多</button>}
      {editing && <MappingDialog initial={editing === "new" ? { customer: customer || "drx" } : editing}
        existing={editing === "new" ? undefined : editing} onClose={() => setEditing(undefined)}
        onSaved={(mapping) => {
          setRevision((value) => value + 1);
          setSaved(`已保存 ${mapping.customer_item} → ${mapping.internal_item}`);
          setEditing(undefined);
        }} />}
    </section>
  );
}
