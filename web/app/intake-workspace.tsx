"use client";

import { DownloadOutlined, FileExcelOutlined, ReloadOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { relayRequest } from "./relay-api";

type Mail = {
  id: number;
  sender: string;
  subject: string;
  received_at: string;
  intake_error_code: string | null;
  attachment_count: number;
};
type Attachment = {
  id: number;
  filename: string;
  size_bytes: number | null;
  intake_state: string;
  job_state: string | null;
  error_code: string | null;
  template: string | null;
};
type Quantity = {
  id: number;
  material: string | null;
  quantity: string;
  quantity_type: string | null;
  unit: string | null;
  requested_date: string | null;
  earliest_date: string | null;
  latest_date: string | null;
  quantity_cell: string | null;
  raw_delivery_date: string | null;
  raw_interval_start: string | null;
  raw_interval_end: string | null;
};
type Detail = { mail: Mail; attachments: Attachment[] };

const errorLabels: Record<string, string> = {
  message_size: "邮件大小超过采集限制",
  invalid_message_content: "邮件内容无法读取",
  archive_integrity: "原件缺失或校验失败",
  archive_io: "原件读取失败",
  invalid_or_unsupported_workbook: "工作簿格式异常或模板尚未支持",
  lease_expired: "解析任务重试次数已用完",
};
const chinaTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "short", hour12: false,
});

function getData<T>(path: string, signal?: AbortSignal): Promise<T> {
  return relayRequest<T>(`/intake${path}`, { signal });
}

function Status({ attachment }: { attachment: Attachment }) {
  if (attachment.job_state === "succeeded") return <span className="status complete">解析完成</span>;
  if (attachment.job_state === "failed") return <span className="status failed">解析失败</span>;
  if (attachment.intake_state === "rejected") return <span className="status failed">归档校验未通过</span>;
  if (attachment.intake_state === "unsupported") return <span className="status queued">已归档</span>;
  return <span className="status queued">{attachment.job_state === "processing" ? "解析中" : "待处理"}</span>;
}

const templateLabels: Record<string, string> = {
  vitesco_delfor: "Vitesco DELFOR", drx_delfor: "DRX DELFOR",
  jlr_delfor: "JLR DELFOR", jlr_deljit: "JLR DELJIT", kroschu_4905: "Kroschu 4905",
};

function deliveryDate(row: Quantity): string {
  if (row.requested_date) return row.requested_date;
  if (row.earliest_date && row.latest_date) return `${row.earliest_date} 至 ${row.latest_date}`;
  if (row.raw_delivery_date) return `${row.raw_delivery_date}（原值）`;
  if (row.raw_interval_start && row.raw_interval_end) {
    return `${row.raw_interval_start} 至 ${row.raw_interval_end}（原值）`;
  }
  return "—";
}

function QuantityTable({ quantities }: { quantities: Quantity[] }) {
  if (quantities.length === 0) return <p className="empty small-empty">暂无原始数量记录</p>;
  return (
    <div className="table-scroll">
      <table>
        <thead><tr>
          <th scope="col">物料编号</th>
          <th scope="col">交付日期 / 区间</th>
          <th scope="col">类型代码</th>
          <th scope="col" className="numeric">原始数量</th>
          <th scope="col">单位</th>
          <th scope="col">来源坐标</th>
        </tr></thead>
        <tbody>{quantities.map((row) => (
          <tr key={row.id}>
            <td className="mono">{row.material ?? "—"}</td>
            <td>{deliveryDate(row)}</td>
            <td className="mono">{row.quantity_type ?? "—"}</td>
            <td className="numeric mono">{row.quantity}</td>
            <td>{row.unit ?? "—"}</td>
            <td className="source mono">{row.quantity_cell ?? "—"}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function IntakeWorkspace() {
  const [messages, setMessages] = useState<Mail[]>([]);
  const [selectedId, setSelectedId] = useState<number>();
  const [detail, setDetail] = useState<Detail>();
  const [selectedAttachment, setSelectedAttachment] = useState<number>();
  const [quantities, setQuantities] = useState<Quantity[]>();
  const [after, setAfter] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loadingQuantities, setLoadingQuantities] = useState(false);
  const [mobileDetail, setMobileDetail] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const selected = messages.find((mail) => mail.id === selectedId) ?? messages[0];
  const visibleDetail = detail?.mail.id === selected?.id ? detail : undefined;

  function showError(reason: unknown) {
    setError(reason instanceof Error ? reason.message : "读取失败，请稍后重试。");
  }

  useEffect(() => {
    const controller = new AbortController();
    getData<Mail[]>("/emails", controller.signal)
      .then(setMessages)
      .catch((reason) => { if (!controller.signal.aborted) showError(reason); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [refresh]);

  useEffect(() => {
    if (selected?.id === undefined) return;
    const controller = new AbortController();
    getData<Detail>(`/emails/${selected.id}`, controller.signal)
      .then(setDetail)
      .catch((reason) => { if (!controller.signal.aborted) showError(reason); });
    return () => controller.abort();
  }, [selected?.id, refresh]);

  useEffect(() => {
    if (selectedAttachment === undefined) return;
    const controller = new AbortController();
    const path = `/attachments/${selectedAttachment}/quantities?after=${after}&limit=100`;
    getData<Quantity[]>(path, controller.signal)
      .then((rows) => {
        setQuantities((previous) => after === 0 ? rows : [...(previous ?? []), ...rows]);
        setHasMore(rows.length === 100);
      })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          showError(reason);
          setHasMore(false);
        }
      })
      .finally(() => { if (!controller.signal.aborted) setLoadingQuantities(false); });
    return () => controller.abort();
  }, [selectedAttachment, after]);

  function selectMail(mail: Mail) {
    setSelectedId(mail.id);
    setMobileDetail(true);
    setQuantities(undefined);
    setSelectedAttachment(undefined);
    setAfter(0);
    setHasMore(false);
    setLoadingQuantities(false);
    setError("");
  }

  function viewQuantities(attachment: Attachment) {
    if (selected) setSelectedId(selected.id);
    setError("");
    setSelectedAttachment(attachment.id);
    setQuantities(undefined);
    setAfter(0);
    setHasMore(false);
    setLoadingQuantities(true);
  }

  async function download(attachment: Attachment) {
    setError("");
    try {
      const response = await fetch(`/api/v1/intake/attachments/${attachment.id}/file`, {
        cache: "no-store",
      });
      if (response.status === 401) window.dispatchEvent(new Event("relay-session-expired"));
      if (!response.ok) throw new Error("原始附件暂不可下载，请检查归档状态。");
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = attachment.filename;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (reason) {
      showError(reason);
    }
  }

  return (
    <main className={`workspace ${mobileDetail && selected ? "detail-open" : ""}`}>
      <aside className="mail-list" aria-label="收件列表">
        <div className="list-heading">
          <div className="heading-row"><h1>收件记录</h1><span className="list-count">最近 {messages.length} 封</span></div>
          <div className="mailbox-actions">
            <button type="button" className="button" disabled={loading} onClick={() => { setError(""); setLoading(true); setRefresh((version) => version + 1); }}>
              <ReloadOutlined aria-hidden="true" /> 刷新
            </button>
          </div>
        </div>
        <div className="mail-items">
          {messages.map((mail) => (
            <button key={mail.id} className={`mail-item ${selected?.id === mail.id ? "selected" : ""}`}
              type="button" aria-pressed={selected?.id === mail.id} onClick={() => selectMail(mail)}>
              <span className="mail-sender">{mail.sender || "未知发件人"}</span>
              <span className="mail-subject">{mail.subject || "无主题"}</span>
              <span className="mail-item-bottom">
                <span className="attachment-count">{mail.attachment_count} 个附件</span>
                <time dateTime={mail.received_at}>{chinaTime.format(new Date(mail.received_at))}</time>
              </span>
            </button>
          ))}
          {messages.length === 0 && <p className="empty">{loading ? "正在读取邮件…" : "暂无归档邮件"}</p>}
        </div>
      </aside>
      <section className="mail-detail" aria-label="收件详情">
        {selected && (
          <div className="detail-header intake-detail-header">
            <button type="button" className="back-button" onClick={() => setMobileDetail(false)}>返回列表</button>
            <div className="detail-title"><h2>{selected.subject || "无主题"}</h2></div>
            <p className="mail-meta">{selected.sender || "未知发件人"} · {chinaTime.format(new Date(selected.received_at))}</p>
          </div>
        )}
        <div className="detail-content">
          {error && <p className="notice failed" role="alert">{error}</p>}
          {selected?.intake_error_code && <p className="notice failed">{errorLabels[selected.intake_error_code] ?? "该邮件未通过采集校验"}</p>}
          {visibleDetail?.attachments.map((attachment) => (
            <section className="intake-attachment" key={attachment.id}>
              <div className="attachment-card">
                <span className="file-icon"><FileExcelOutlined aria-hidden="true" /></span>
                <div className="file-info">
                  <p className="filename">{attachment.filename}</p>
                  <p className="file-meta">{attachment.template ? (templateLabels[attachment.template] ?? attachment.template) : "附件"}{attachment.size_bytes !== null && ` · ${Math.ceil(attachment.size_bytes / 1024)} KB`}</p>
                </div>
                <Status attachment={attachment} />
                {attachment.size_bytes !== null && <button type="button" className="button" onClick={() => void download(attachment)}>
                  <DownloadOutlined aria-hidden="true" /> 下载原件
                </button>}
                {attachment.job_state === "succeeded" && <button type="button" className="button" disabled={selectedAttachment === attachment.id} onClick={() => viewQuantities(attachment)}>原始数量</button>}
              </div>
              {attachment.error_code && <p className="field-hint">{errorLabels[attachment.error_code] ?? "任务未能完成"}</p>}
              {attachment.id === selectedAttachment && quantities && <QuantityTable quantities={quantities} />}
              {attachment.id === selectedAttachment && loadingQuantities && <p className="empty">正在读取记录…</p>}
              {attachment.id === selectedAttachment && hasMore && quantities && (
                <div className="mailbox-actions">
                  <button type="button" className="button" disabled={loadingQuantities}
                    onClick={() => {
                      setLoadingQuantities(true);
                      setAfter(quantities[quantities.length - 1].id);
                    }}>
                    加载更多
                  </button>
                </div>
              )}
            </section>
          ))}
          {selected && !visibleDetail && !error && <p className="empty">正在读取邮件详情…</p>}
          {visibleDetail?.attachments.length === 0 && <p className="empty">该邮件没有归档附件</p>}
        </div>
      </section>
    </main>
  );
}
