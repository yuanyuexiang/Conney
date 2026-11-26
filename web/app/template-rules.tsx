"use client";

import { ArrowLeftOutlined, FileExcelOutlined, ExperimentOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useState } from "react";
import { messageOf, relayRequest } from "./relay-api";
import type { DrxRules, DrxRuleVersion, EtaChange, RuleImpact, TemplateAttachment, WorkbookTemplate } from "./template-rules-api";
import { WorkbenchSelect } from "./workbench-select";
import { chinaTime } from "./workbench-status";

function EtaTable({ rows, actual }: { rows: EtaChange[]; actual?: boolean }) {
  return <div className="table-scroll rule-impact-table"><table>
    <thead><tr>
      {actual && <th scope="col">客户料号 / 来源</th>}
      <th scope="col">运输</th><th scope="col">发货日</th><th scope="col">原 ETA</th><th scope="col">试算 ETA</th>
    </tr></thead>
    <tbody>{rows.map((row, index) => <tr key={index}>
      {actual && <td>{row.customer_item ?? "—"} · {row.quantity_cell ?? "—"}</td>}
      <td>{row.mode === "air" ? "空运" : "海运"}</td><td>{row.ship_date}</td><td>{row.before}</td><td>{row.after}</td>
    </tr>)}</tbody>
  </table></div>;
}

export function RuleImpactView({ impact }: { impact: RuleImpact }) {
  return <div className="rule-impact">
    <p><strong>{impact.filename}</strong></p>
    <p className="field-hint">
      {impact.document_no && `报文 ${impact.document_no} · `}
      解析版本 {impact.parser_version}{impact.sample_revision != null && ` · 草稿版本 ${impact.sample_revision}`}
    </p>
    <dl className="rule-facts">
      <div><dt>物料</dt><dd>{impact.material_count} 个</dd></div>
      <div><dt>原始数量记录</dt><dd>{impact.quantity_count} 条</dd></div>
      <div><dt>待核对需求</dt><dd>{impact.pending_entries} 条</dd></div>
      <div><dt>待安排发货</dt><dd>{impact.pending_shipments} 条</dd></div>
    </dl>
    {impact.simulated_etas.length > 0 && <>
      <h4>模拟发货日试算</h4>
      <EtaTable rows={impact.simulated_etas} />
    </>}
    <h4>已安排发货中，ETA 变化 {impact.changed_eta_count} 条</h4>
    {impact.changed_etas.length > 0 && <>
      {impact.changed_eta_count > impact.changed_etas.length &&
        <p className="field-hint">显示前 {impact.changed_etas.length} 条变化。</p>}
      <EtaTable rows={impact.changed_etas} actual />
    </>}
  </div>;
}

function RuleTrialForm({ rules, source, busy, onTry }: {
  rules: DrxRules; source?: TemplateAttachment; busy: boolean; onTry: (values: object) => void;
}) {
  const [sampleId, setSampleId] = useState(() => String(
    rules.samples.find((sample) => sample.attachment_id === source?.attachmentId)?.attachment_id ??
    rules.samples[0]?.attachment_id ?? "",
  ));
  const sample = rules.samples.find(({ attachment_id }) => String(attachment_id) === sampleId);
  const sampleDate = sample?.document_date ?? "";
  const date = /^\d{8}$/.test(sampleDate)
    ? `${sampleDate.slice(0, 4)}-${sampleDate.slice(4, 6)}-${sampleDate.slice(6)}` : "";

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !sample) return;
    const form = new FormData(event.currentTarget);
    onTry({
      baseline_id: rules.active.id, attachment_id: sample.attachment_id,
      simulated_date: form.get("simulated_date"), source_note: form.get("source_note"),
      parameters: { air_days: Number(form.get("air_days")), sea_days: Number(form.get("sea_days")) },
    });
  }

  return <form className="rule-trial-form account-form" onSubmit={submit}>
    <label>空运时效（天）<input type="number" name="air_days" min={0} max={365} required defaultValue={rules.active.parameters.air_days} /></label>
    <label>海运时效（天）<input type="number" name="sea_days" min={0} max={365} required defaultValue={rules.active.parameters.sea_days} /></label>
    <label>验收样本
      <WorkbenchSelect label="验收样本" value={sampleId} onChange={setSampleId} disabled={busy}
        options={rules.samples.map((item) => ({ value: String(item.attachment_id), label: item.filename }))} />
    </label>
    <label>试算发货日<input key={sampleId} type="date" name="simulated_date" required defaultValue={date} /></label>
    <label className="rule-trial-wide">变更依据<input name="source_note" required maxLength={240} placeholder="例如运输计划编号或客户确认邮件" /></label>
    <div className="rule-trial-wide">
      <button type="submit" className="button primary-button" disabled={busy || !sample}>
        <ExperimentOutlined aria-hidden="true" /> {busy ? "正在试算…" : "试算并保存草案"}
      </button>
    </div>
  </form>;
}

function DrxRulePanel({ admin, source }: { admin: boolean; source?: TemplateAttachment }) {
  const [rules, setRules] = useState<DrxRules>();
  const [selected, setSelected] = useState<DrxRuleVersion>();
  const [revision, setRevision] = useState(0);
  const [busy, setBusy] = useState<"trial" | "publish">();
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const sampleId = source?.attachmentId;
  useEffect(() => {
    const controller = new AbortController();
    const path = sampleId === undefined ? "/templates/drx/rules" : `/templates/drx/rules?sample_id=${sampleId}`;
    relayRequest<DrxRules>(path, { signal: controller.signal })
      .then(values => { if (!controller.signal.aborted) setRules(values); })
      .catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [revision, sampleId]);

  async function tryRule(values: object) {
    setBusy("trial");
    setError("");
    setMessage("");
    try {
      const candidate = await relayRequest<DrxRuleVersion>("/templates/drx/rules/preview", {
        method: "POST", body: JSON.stringify(values),
      });
      setSelected(candidate);
      setRevision(value => value + 1);
    } catch (reason) {
      setError(messageOf(reason));
      setRevision(value => value + 1);
    } finally { setBusy(undefined); }
  }

  async function publish() {
    if (!selected || busy) return;
    setBusy("publish");
    setError("");
    try {
      const published = await relayRequest<DrxRuleVersion>(`/templates/drx/rules/${selected.id}/publish`, { method: "POST" });
      setMessage(`规则 ${published.version} 已发布，新建草稿将使用该版本。`);
      setSelected(undefined);
      setRevision(value => value + 1);
    } catch (reason) {
      setError(messageOf(reason));
      setRevision(value => value + 1);
    } finally { setBusy(undefined); }
  }

  if (!rules) return error
    ? <p className="notice failed" role="alert">{error}</p>
    : <p className="empty" role="status">正在读取 DRX 规则版本…</p>;
  const canPublish = admin && selected?.trial && !selected.published_at && selected.baseline_id === rules.active.id;
  return <>
    <section className="page-card template-panel" aria-label="生效的转换规则">
      <div className="section-heading"><h3>生效的转换规则</h3><span className="status complete">{rules.active.version}</span></div>
      <dl className="rule-facts">
        <div><dt>空运时效</dt><dd>{rules.active.parameters.air_days} 天</dd></div>
        <div><dt>海运时效</dt><dd>{rules.active.parameters.sea_days} 天</dd></div>
        <div><dt>变更依据</dt><dd>{rules.active.source_note}</dd></div>
      </dl>
    </section>
    {admin && <section className="page-card template-panel">
      <h3>试算规则</h3>
      {rules.samples.length > 0 ? <RuleTrialForm key={`${rules.active.id}:${source?.attachmentId ?? ""}`} rules={rules} source={source}
        busy={busy === "trial"} onTry={values => void tryRule(values)} />
        : <p className="field-hint">收件档案有已解析的 DRX 附件后可试算。</p>}
      {message && <p className="notice" role="status">{message}</p>}
      {error && !selected && <p className="notice failed" role="alert">{error}</p>}
    </section>}
    <section className="page-card template-panel" aria-label="规则版本记录">
      <h3>规则版本</h3>
      <ul className="rule-version-list">{rules.versions.map(version => (
        <li key={version.id}>
          <div>
            <strong>{version.version}</strong>{" "}
            <span className={`delivery-badge ${version.published_at ? "" : "review"}`}>
              {version.id === rules.active.id ? "生效中" : version.published_at ? "历史版本" :
                version.baseline_id === rules.active.id ? "已试算 · 待发布" : "需重新试算"}
            </span>
            <p>空运 {version.parameters.air_days} 天 · 海运 {version.parameters.sea_days} 天 · {version.source_note}</p>
            <small>{chinaTime.format(new Date(version.published_at ?? version.created_at))}
              {(version.published_by ?? version.created_by) && ` · ${version.published_by ?? version.created_by}`}</small>
          </div>
          {version.trial && <button type="button" className="button" onClick={() => { setError(""); setSelected(version); }}>
            查看试算
          </button>}
        </li>
      ))}</ul>
    </section>
    {selected?.trial && <Modal open width={720} centered title={`规则 ${selected.version} · 试算结果`}
      footer={<>
        <button type="button" className="button" disabled={Boolean(busy)} onClick={() => setSelected(undefined)}>关闭</button>
        {canPublish && <button type="button" className="button primary-button" disabled={Boolean(busy)} onClick={() => void publish()}>
          {busy === "publish" ? "正在发布…" : "确认并发布"}
        </button>}
      </>} mask={{ closable: false }} keyboard={!busy} closable={!busy} onCancel={() => setSelected(undefined)}>
      <p className="field-hint">对照 drx-{selected.baseline_id} · 空运 {selected.parameters.air_days} 天 · 海运 {selected.parameters.sea_days} 天</p>
      <RuleImpactView impact={selected.trial} />
      {error && <p className="notice failed" role="alert">{error}</p>}
    </Modal>}
  </>;
}

export function TemplateRulesPage({ admin, source, onArchive }: {
  admin: boolean; source?: TemplateAttachment; onArchive: (mailId: number) => void;
}) {
  const [catalog, setCatalog] = useState<WorkbookTemplate[]>();
  const [selected, setSelected] = useState(source?.code ?? "drx_delfor");
  const [error, setError] = useState("");
  const [archive, setArchive] = useState<TemplateAttachment[]>([]);
  const [archiveError, setArchiveError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    relayRequest<WorkbookTemplate[]>("/templates", { signal: controller.signal })
      .then(values => { if (!controller.signal.aborted) setCatalog(values); })
      .catch(reason => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    if (!source || source.mailId === undefined || source.filename) return;
    const controller = new AbortController();
    const { mailId, code } = source;
    relayRequest<{ attachments: { id: number; filename: string; template: string | null; parser_version: string | null }[] }>(
      `/intake/emails/${mailId}`, { signal: controller.signal },
    ).then(({ attachments }) => {
      if (!controller.signal.aborted) setArchive(attachments.filter(item => item.template === code).map(item => ({
        code, mailId, attachmentId: item.id, filename: item.filename, parserVersion: item.parser_version,
      })));
    }).catch(reason => { if (!controller.signal.aborted) setArchiveError(messageOf(reason)); });
    return () => controller.abort();
  }, [source]);
  const template = catalog?.find(({ code }) => code === selected) ?? catalog?.[0];
  if (error) return <p className="notice failed" role="alert">{error}</p>;
  if (!template) return <p className="empty" role="status">正在读取模板目录…</p>;
  const linked = source?.code === template.code ? source : undefined;
  const mailId = linked?.mailId;
  const linkedFiles = linked?.filename ? [linked] : linked ? archive : [];
  return <div className="template-workbench">
    <aside className="page-card template-index" aria-label="合同模板">
      {catalog?.map(item => <button type="button" className="template-index-item"
        aria-pressed={template.code === item.code} key={item.code} onClick={() => setSelected(item.code)}>
        <strong>{item.name}</strong><small>{item.customer}</small>
        <span className="delivery-badge">{item.output_version ? "可生成交付表" : "可解析"}</span>
      </button>)}
    </aside>
    <div className="template-content">
      <section className="page-card template-panel">
        <div className="section-heading"><h2><FileExcelOutlined aria-hidden="true" /> {template.name}</h2>
          {mailId !== undefined && <button type="button" className="text-button"
            onClick={() => onArchive(mailId)}><ArrowLeftOutlined /> 返回原邮件</button>}
        </div>
        {linkedFiles.map(file => <p key={file.attachmentId} className="template-linked">
          <strong>{file.filename}</strong>{file.parserVersion && ` · 解析版本 ${file.parserVersion}`}
        </p>)}
        {linked && archiveError && <p className="notice failed" role="alert">归档来源读取失败。{archiveError}</p>}
        <dl className="rule-facts">
          <div><dt>识别依据</dt><dd>{template.recognition}</dd></div>
          <div><dt>关键校验字段</dt><dd>{template.required_fields.join("、")}</dd></div>
          <div><dt>输出模板</dt><dd>{template.output_version
            ? `${template.output_sheets.join("、")} · ${template.output_version}` : "交付表待接入"}</dd></div>
        </dl>
        <h3>转换口径</h3>
        <ul className="template-rule-notes">{template.rule_notes.map(note => <li key={note}>{note}</li>)}</ul>
      </section>
      {template.code === "drx_delfor" && <DrxRulePanel admin={admin} source={linkedFiles.length === 1 ? linkedFiles[0] : linked} />}
    </div>
  </div>;
}
