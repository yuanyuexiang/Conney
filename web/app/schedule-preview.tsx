"use client";

import { ReloadOutlined } from "@ant-design/icons";
import { useState } from "react";
import { addQuantities, mappingLabels, type DeliveryDraft, type Material } from "./delivery-api";

const pageSize = 35;
const aids = {
  drx_delfor: "",
  kroschu_4905: "Excel 包含按交货日、ISO 周和 ETD 月份的汇总。特殊日期在待核对中保留。",
  jlr_delfor: "周需求包含日期明细和累计差分。跨周区间保留原区间及总量。",
  jlr_deljit: "按近期报文汇总周需求，累计值和其他数量见需求与来源。",
  vitesco_delfor: "周预测保留原报文空白与零值。库存测算可填写期初库存、实际产量及产能。",
} as const;

export function SchedulePreview({ draft, admin, busy, forReview, onMatch, onRefresh }: {
  draft: DeliveryDraft; admin: boolean; busy: boolean; forReview: boolean;
  onMatch: (material: Material) => void; onRefresh: () => void;
}) {
  const [tab, setTab] = useState<"demand" | "review" | "source">(forReview ? "review" : "demand");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const search = query.toLowerCase().trim();
  const materials = draft.materials.filter(material => !search || [
    material.customer_item, material.order_no, material.mapping?.internal_item, material.ship_to,
  ].some(value => value?.toLowerCase().includes(search)));
  const unmapped = materials.filter(material => material.mapping_state !== "matched");
  const rows = materials.flatMap(material => material.entries.flatMap(entry => {
    const plan = entry.plan;
    if (!plan) return [];
    if (tab === "demand" && plan.kind !== "day" && plan.kind !== "week") return [];
    if (tab === "review" && !plan.issue) return [];
    return [{ material, entry, plan }];
  }));
  const lastPage = Math.max(0, Math.ceil(rows.length / pageSize) - 1);
  const visiblePage = Math.min(lastPage, page);
  const visible = rows.slice(visiblePage * pageSize, (visiblePage + 1) * pageSize);

  return <>
    <div className="delivery-view-bar">
      <div className="delivery-view-tabs" role="group" aria-label="交付草稿视图">
        {([["demand", "需求明细"], ["review", "待核对"], ["source", "原报文记录"]] as const).map(([key, label]) =>
          <button type="button" key={key} aria-pressed={tab === key}
            onClick={() => { setTab(key); setPage(0); }}>
            {label}{key === "review" && draft.pending_mappings + draft.pending_entries > 0 &&
              <span>{draft.pending_mappings + draft.pending_entries}</span>}
          </button>
        )}
      </div>
      {admin && <button type="button" className="button" disabled={busy} onClick={onRefresh}>
        <ReloadOutlined aria-hidden="true" /> 重新匹配
      </button>}
    </div>
    <div className="schedule-preview">
      <div className="schedule-tools">
        <input type="search" aria-label="搜索草稿物料" placeholder="搜索料号、订单或收货方" value={query} maxLength={80}
          onChange={event => { setQuery(event.target.value); setPage(0); }} />
        <p className="field-hint">{aids[draft.template]}</p>
      </div>
      {tab === "review" && unmapped.length > 0 && <section className="schedule-mapping-list" aria-label="物料核对">
        <h3>物料核对</h3>
        {unmapped.map(material => <div key={material.id} className="delivery-review-row">
          <div><strong>{material.customer_item ?? "未知料号"}</strong>
            <small>{[material.order_no, material.ship_to, material.customer_item_cell].filter(Boolean).join(" · ")}</small>
          </div>
          <span className="delivery-badge review">{mappingLabels[material.mapping_state]}</span>
          {admin && material.customer_item && material.order_no && material.mapping_state !== "ambiguous" &&
            <button type="button" className="text-button" disabled={busy} onClick={() => onMatch(material)}>
              {material.mapping ? "查看映射" : "补充映射"}
            </button>}
        </div>)}
      </section>}
      {visible.length ? <div className="table-scroll schedule-table" tabIndex={0} role="region" aria-label="草稿数量记录">
        <table>
          <thead><tr>
            <th scope="col">康尼料号</th><th scope="col">客户料号 / 订单</th>
            <th scope="col">交货日期 / 周起始</th><th scope="col" className="numeric">计算数量</th>
            <th scope="col" className="numeric">原始数量</th><th scope="col">口径与来源</th>
          </tr></thead>
          <tbody>{visible.map(({ material, entry, plan }) => (
            <tr key={entry.id}>
              <td>{material.mapping_state === "matched" && material.mapping
                ? material.mapping.internal_item : <span className="delivery-badge review">{mappingLabels[material.mapping_state]}</span>}
              </td>
              <td><strong>{material.customer_item ?? "—"}</strong><small>{material.order_no ?? "—"}</small></td>
              <td>{plan.start ?? plan.raw_start ?? "—"}{plan.end && plan.kind === "interval" && <> 至 {plan.end}</>}</td>
              <td className="numeric mono">{plan.amount === null ? "—" : addQuantities([{ quantity: plan.amount }])}
                {plan.amount !== null && entry.unit ? <small>{entry.unit}</small> : null}</td>
              <td className="numeric mono">{entry.raw_quantity ?? entry.quantity}</td>
              <td>
                {plan.issue && <strong className="schedule-issue">{plan.issue}</strong>}
                <details><summary>{entry.quantity_cell ?? "查看来源"}</summary>
                  <p>{plan.basis}</p>
                  {plan.raw_start && <p>日期原值 {plan.raw_start}{plan.raw_end ? ` 至 ${plan.raw_end}` : ""}</p>}
                </details>
              </td>
            </tr>
          ))}</tbody>
        </table>
      </div> : <p className="empty">{tab === "review" ? "没有待核对的数量记录" : "没有符合条件的数量记录"}</p>}
      {lastPage > 0 && <div className="delivery-date-pages" aria-label="数量分页">
        <button type="button" className="button" disabled={visiblePage === 0} onClick={() => setPage(visiblePage - 1)}>上一页</button>
        <span>第 {visiblePage + 1} / {lastPage + 1} 页 · {rows.length} 条记录</span>
        <button type="button" className="button" disabled={visiblePage === lastPage} onClick={() => setPage(visiblePage + 1)}>下一页</button>
      </div>}
    </div>
  </>;
}
