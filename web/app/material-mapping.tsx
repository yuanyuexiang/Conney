"use client";

import { PlusOutlined, SearchOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useState } from "react";
import { customerLabels, type Customer, type MappingValues, type MaterialMapping } from "./delivery-api";
import { messageOf, relayRequest } from "./relay-api";

const customers = Object.entries(customerLabels) as [Customer, string][];
const identifierFields = [
  ["customer_item", "客户料号"],
  ["internal_item", "康尼料号"],
  ["order_no", "采购订单"],
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

export function MappingDialog({ initial, existing, onClose, onSaved }: {
  initial: Partial<MappingValues>;
  existing?: MaterialMapping;
  onClose: () => void;
  onSaved: (mapping: MaterialMapping) => void;
}) {
  const [customer, setCustomer] = useState<Customer>(initial.customer ?? "drx");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const form = new FormData(event.currentTarget);
    const identifiers = Object.fromEntries(identifierFields.map(([key]) => [key, String(form.get(key) ?? "").trim() || null]));
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

  return (
    <Modal open title={existing ? "编辑物料匹配" : "新增物料匹配"} width={660} centered footer={null}
      closable={saving ? false : { "aria-label": "关闭物料匹配" }} keyboard={!saving}
      mask={{ closable: false }} onCancel={() => { if (!saving) onClose(); }}>
      <form className="account-form delivery-form" onSubmit={(event) => void save(event)}>
        <fieldset disabled={saving} className="delivery-form-fields">
          <label>客户<select value={customer} onChange={(event) => setCustomer(event.target.value as Customer)}>
            {customers.map(([key, label]) => <option value={key} key={key}>{label}</option>)}
          </select></label>
          {identifierFields.map(([key, label]) => (
            <label key={key}>{label}
              <input name={key} defaultValue={initial[key] ?? ""} maxLength={80}
                required={key === "customer_item" || key === "internal_item" || (key === "order_no" && customer === "drx")} />
            </label>
          ))}
          {quantityFields.map(([key, label]) => (
            <label key={key}>{label}<input name={key} type="number" step={1} min={key === "safety_stock" ? 0 : 1}
              max={1000000000} defaultValue={initial[key] ?? ""} /></label>
          ))}
        </fieldset>
        {error && <p className="notice failed" role="alert">{error}</p>}
        <div className="account-form-actions">
          <button className="button" type="button" onClick={onClose} disabled={saving}>取消</button>
          <button className="button primary-button" type="submit" disabled={saving}>{saving ? "正在保存…" : "保存映射"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function MaterialMappingPage() {
  const [rows, setRows] = useState<MaterialMapping[]>();
  const [q, setQ] = useState("");
  const [customer, setCustomer] = useState("");
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
          <select value={customer} onChange={(event) => setCustomer(event.target.value)} aria-label="按客户筛选">
            <option value="">全部客户</option>
            {customers.map(([key, label]) => <option value={key} key={key}>{label}</option>)}
          </select>
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
      {editing && <MappingDialog initial={editing === "new" ? { customer: customer as Customer || "drx" } : editing}
        existing={editing === "new" ? undefined : editing} onClose={() => setEditing(undefined)}
        onSaved={(mapping) => {
          setRevision((value) => value + 1);
          setSaved(`已保存 ${mapping.customer_item} → ${mapping.internal_item}`);
          setEditing(undefined);
        }} />}
    </section>
  );
}
