"use client";

import {
  ArrowLeftOutlined, ArrowRightOutlined, CheckOutlined, ClockCircleOutlined, CloudDownloadOutlined,
  DatabaseOutlined, DownloadOutlined, ExclamationCircleOutlined, FileExcelOutlined, FileSearchOutlined,
  LinkOutlined, MailOutlined, ReloadOutlined, SearchOutlined,
} from "@ant-design/icons";
import { useEffect, useState } from "react";
import { messageOf, relayRequest } from "./relay-api";
import { chinaTime, formatSyncTime, type IntakeCounts, type MailboxStatus, type SyncView } from "./workbench-status";

type MailState = "failed" | "review" | "queued" | "complete" | "archived";
type MailFilter = MailState | "all" | "actionable";
type Mail = {
  id: number;
  sender: string;
  subject: string;
  received_at: string;
  intake_error_code: string | null;
  attachment_count: number;
  templates: string[];
  state: MailState;
};
type Attachment = {
  id: number;
  filename: string;
  size_bytes: number | null;
  intake_state: string;
  job_state: string | null;
  error_code: string | null;
  error_reason: string | null;
  error_location: string | null;
  review_location: string | null;
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
  delivery_date_cell: string | null;
  raw_delivery_date: string | null;
  raw_interval_start: string | null;
  raw_interval_end: string | null;
};
type Detail = { mail: Mail; attachments: Attachment[] };
type MailPage = { key: string; fetchedFor: string; rows: Mail[]; more: boolean };

const errorLabels: Record<string, string> = {
  message_size: "邮件大小超过采集限制",
  invalid_message_content: "邮件内容无法读取",
  archive_integrity: "原件缺失或校验失败",
  archive_io: "原件读取失败",
  invalid_or_unsupported_workbook: "工作簿格式异常或模板尚未支持",
  lease_expired: "解析任务重试次数已用完",
};

const failureReasons: Record<string, string> = {
  "invalid quantity": "数量栏不是有效数值",
  "quantity is out of range": "数量超出支持范围",
  "invalid delivery date": "交付日期无效",
  "unsupported delivery date": "交付日期格式无法识别",
  "missing delivery date": "缺少交付日期",
  "missing delivery interval endpoint": "交付区间缺少起止日期",
  "inverted delivery interval": "交付区间的开始日期晚于结束日期",
  "invalid delivery interval": "交付区间无效",
  "unexpected formula": "工作簿含有公式",
  "missing material number": "缺少物料编号",
  "Unrecognized schedule workbook title": "无法识别报文模板",
};

const templateLabels: Record<string, string> = {
  vitesco_delfor: "Vitesco DELFOR", drx_delfor: "DRX DELFOR",
  jlr_delfor: "JLR DELFOR", jlr_deljit: "JLR DELJIT", kroschu_4905: "Kroschu 4905",
};

const statusLabels: Record<MailState, string> = {
  failed: "有异常", review: "待核对", queued: "解析排队中", complete: "解析完成", archived: "已归档",
};

const queueFilters = [
  { id: "failed", label: "采集与解析异常", icon: ExclamationCircleOutlined },
  { id: "review", label: "待人工核对", icon: FileSearchOutlined },
  { id: "queued", label: "解析队列", icon: ClockCircleOutlined },
] as const;

const archiveFilters = [
  { id: "all", label: "全部状态" },
  { id: "complete", label: "解析完成" },
  { id: "archived", label: "仅归档" },
  { id: "failed", label: "有异常" },
  { id: "review", label: "待核对" },
  { id: "queued", label: "排队中" },
] as const;

const MAIL_PAGE_SIZE = 30;
const QUANTITY_PAGE_SIZE = 100;

function getMailPage(state: MailFilter, query: string, signal?: AbortSignal, before?: number) {
  const params = new URLSearchParams({ state, q: query, limit: String(MAIL_PAGE_SIZE + 1) });
  if (before !== undefined) params.set("before", String(before));
  return relayRequest<Mail[]>(`/intake/emails?${params}`, { signal });
}

function Status({ state, children }: { state: MailState; children?: string }) {
  return <span className={`status ${state}`}>{children ?? statusLabels[state]}</span>;
}

function AttachmentStatus({ attachment }: { attachment: Attachment }) {
  if (attachment.intake_state === "rejected") return <Status state="failed">归档校验未通过</Status>;
  if (attachment.job_state === "failed") return <Status state="failed">解析失败</Status>;
  if (attachment.review_location) return <Status state="review" />;
  if (attachment.job_state === "succeeded") return <Status state="complete" />;
  if (attachment.intake_state === "unsupported") return <Status state="archived" />;
  return <Status state="queued">{attachment.job_state === "processing" ? "解析中" : "排队中"}</Status>;
}

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
  if (quantities.length === 0) return <p className="empty">暂无原始数量记录</p>;
  return (
    <div className="table-scroll" tabIndex={0} role="region" aria-label="原始数量记录">
      <table>
        <thead><tr>
          <th scope="col">物料编号</th>
          <th scope="col">交付日期 / 区间</th>
          <th scope="col">类型代码</th>
          <th scope="col" className="numeric">原始数量</th>
          <th scope="col">单位</th>
          <th scope="col">数量坐标</th>
        </tr></thead>
        <tbody>{quantities.map((row) => {
          const special = row.raw_delivery_date === "000000";
          return (
            <tr key={row.id}>
              <td className="mono">{row.material ?? "—"}</td>
              <td>{special ? <>
                <Status state="review">{deliveryDate(row)}</Status>
                {row.delivery_date_cell && <span className="source mono date-location">{row.delivery_date_cell}</span>}
              </> : deliveryDate(row)}</td>
              <td className="mono">{row.quantity_type ?? "—"}</td>
              <td className="numeric mono">{row.quantity}</td>
              <td>{row.unit ?? "—"}</td>
              <td className="source mono">{row.quantity_cell ?? "—"}</td>
            </tr>
          );
        })}</tbody>
      </table>
    </div>
  );
}

function AttachmentQuantities({ attachmentId }: { attachmentId: number }) {
  const [quantities, setQuantities] = useState<Quantity[]>([]);
  const [after, setAfter] = useState(0);
  const [more, setMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<Quantity[]>(`/intake/attachments/${attachmentId}/quantities?after=${after}&limit=${QUANTITY_PAGE_SIZE + 1}`, {
      signal: controller.signal,
    }).then((rows) => {
      if (controller.signal.aborted) return;
      setQuantities((previous) => [...previous, ...rows.slice(0, QUANTITY_PAGE_SIZE)]);
      setMore(rows.length > QUANTITY_PAGE_SIZE);
    }).catch((reason) => {
      if (!controller.signal.aborted) setError(messageOf(reason));
    }).finally(() => {
      if (!controller.signal.aborted) setLoading(false);
    });
    return () => controller.abort();
  }, [attachmentId, after, revision]);

  return (
    <div className="attachment-quantities">
      <h4>原始数量记录</h4>
      {(quantities.length > 0 || (!loading && !error)) && <QuantityTable quantities={quantities} />}
      {loading && <p className="empty" role="status">正在读取记录…</p>}
      {error && <p className="notice failed" role="alert">{error}</p>}
      {error && <button type="button" className="button" onClick={() => {
        setError("");
        setLoading(true);
        setRevision((previous) => previous + 1);
      }}>重试</button>}
      {!error && more && !loading && <button type="button" className="button" onClick={() => {
        setLoading(true);
        setAfter(quantities[quantities.length - 1].id);
      }}>加载更多记录</button>}
    </div>
  );
}

function Diagnosis({ attachment }: { attachment: Attachment }) {
  if (attachment.review_location) return (
    <div className="diagnosis review">
      <strong>交付日期原值待核对</strong>
      <p>源文件中的 <code>000000</code> 已保留，请按客户规则核对。</p>
      <details>
        <summary>查看来源</summary>
        <dl><dt>Excel 坐标</dt><dd><code>{attachment.review_location}</code></dd></dl>
      </details>
    </div>
  );

  if (!attachment.error_code || attachment.job_state !== "failed") return null;
  const label = failureReasons[attachment.error_reason ?? ""] ?? errorLabels[attachment.error_code] ?? "附件解析未完成";
  return (
    <div className="diagnosis failed">
      <strong>{label}</strong>
      {attachment.error_location && (
        <details>
          <summary>查看来源</summary>
          <dl><dt>Excel 坐标</dt><dd><code>{attachment.error_location}</code></dd></dl>
        </details>
      )}
    </div>
  );
}

function MailDetail({ mail, refreshKey, onBack, archive }: {
  mail: Mail; refreshKey: string; onBack: () => void; archive: boolean;
}) {
  const [detail, setDetail] = useState<Detail>();
  const [selectedAttachment, setSelectedAttachment] = useState<number>();
  const [error, setError] = useState("");
  const [downloadError, setDownloadError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<Detail>(`/intake/emails/${mail.id}`, { signal: controller.signal })
      .then((values) => {
        if (controller.signal.aborted) return;
        setDetail(values);
        setError("");
      })
      .catch((reason) => { if (!controller.signal.aborted) setError(messageOf(reason)); });
    return () => controller.abort();
  }, [mail.id, refreshKey]);

  async function download(attachment: Attachment) {
    setDownloadError("");
    try {
      const response = await fetch(`/api/v1/intake/attachments/${attachment.id}/file`, { cache: "no-store" });
      if (response.status === 401) window.dispatchEvent(new Event("relay-session-expired"));
      if (!response.ok) throw new Error("原始附件暂不可下载，请检查归档状态。");
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = attachment.filename;
      anchor.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch {
      setDownloadError("原始附件暂不可下载，请检查归档状态。");
    }
  }

  const visible = detail?.mail ?? mail;
  return (
    <section className="mail-detail" aria-label="邮件详情">
      <div className="detail-header intake-detail-header">
        {!archive && <button type="button" className="back-button" onClick={onBack}><ArrowLeftOutlined aria-hidden="true" /> 返回列表</button>}
        <div className="detail-title"><h2>{visible.subject || "无主题"}</h2><Status state={visible.state} /></div>
        <p className="mail-meta">
          <span>{visible.sender || "未知发件人"}</span>
          <time dateTime={visible.received_at}>{chinaTime.format(new Date(visible.received_at))}</time>
        </p>
      </div>
      <div className="detail-content">
        {error && <p className="notice failed" role="alert">{error}</p>}
        {downloadError && <p className="notice failed" role="alert">{downloadError}</p>}
        {visible.intake_error_code && <p className="notice failed">{errorLabels[visible.intake_error_code] ?? "该邮件未通过采集校验"}</p>}
        {detail && <div className="section-heading attachment-heading"><h3>归档附件</h3><span>{detail.attachments.length} 个</span></div>}
        {detail?.attachments.map((attachment) => (
          <section className="intake-attachment" key={attachment.id}>
            <div className="attachment-card">
              <span className="file-icon"><FileExcelOutlined aria-hidden="true" /></span>
              <div className="file-info">
                <p className="filename">{attachment.filename}</p>
                <p className="file-meta">
                  {attachment.template ? (templateLabels[attachment.template] ?? attachment.template) : "附件"}
                  {attachment.size_bytes !== null && ` · ${Math.max(1, Math.ceil(attachment.size_bytes / 1024))} KB`}
                </p>
              </div>
              <AttachmentStatus attachment={attachment} />
            </div>
            <Diagnosis attachment={attachment} />
            {attachment.size_bytes !== null && (
              <div className="attachment-actions">
                {attachment.job_state === "succeeded" && <button type="button" className="button"
                  aria-expanded={selectedAttachment === attachment.id}
                  onClick={() => setSelectedAttachment(selectedAttachment === attachment.id ? undefined : attachment.id)}>
                  {selectedAttachment === attachment.id ? "收起原始数量" : "查看原始数量"}
                </button>}
                <button type="button" className="button" onClick={() => void download(attachment)}>
                  <DownloadOutlined aria-hidden="true" /> 下载原件
                </button>
              </div>
            )}
            {selectedAttachment === attachment.id && <AttachmentQuantities attachmentId={attachment.id} />}
          </section>
        ))}
        {!detail && !error && <p className="empty" role="status">正在读取邮件详情…</p>}
        {detail?.attachments.length === 0 && <p className="empty small-empty">该邮件没有归档附件</p>}
      </div>
    </section>
  );
}

const onboardingSteps = [
  { title: "填写业务邮箱", description: "填写 263 邮箱、授权码、文件夹和历史采集起始日期。", icon: MailOutlined },
  { title: "测试连接", description: "确认系统可以通过 IMAP 只读访问指定文件夹。", icon: LinkOutlined },
  { title: "启动采集", description: "启用采集后，在收件档案中查看邮件和解析结果。", icon: CloudDownloadOutlined },
];

function ConnectMailbox({ sync, onConfigure }: { sync: SyncView; onConfigure?: () => void }) {
  const step = sync.kind === "setup" ? 0 : sync.kind === "test" ? 1 : 2;
  return (
    <div className={`onboarding ${onConfigure ? "with-steps" : ""}`}>
      <section className="onboarding-main">
        <div className="empty-mark"><MailOutlined aria-hidden="true" /></div>
        <h2>{onConfigure ? (step === 0 ? "接入业务邮箱" : "完成邮箱接入") : "等待管理员接入业务邮箱"}</h2>
        <p>启用采集后，异常邮件和需要核对的交付记录会进入待处理队列。</p>
        {onConfigure && <button type="button" className="button primary-button" onClick={onConfigure}>
          {step === 0 ? "配置邮箱" : "继续配置"} <ArrowRightOutlined aria-hidden="true" />
        </button>}
      </section>
      {onConfigure && <ol className="onboarding-steps" aria-label="接入步骤">
        {onboardingSteps.map(({ title, description, icon: Icon }, index) => (
          <li key={title} aria-current={step === index ? "step" : undefined}>
            <span className="step-icon">
              {index < step ? <CheckOutlined aria-label="已完成" /> : <Icon aria-hidden="true" />}
            </span>
            <div><h3>{title}</h3><p>{description}</p></div>
          </li>
        ))}
      </ol>}
    </div>
  );
}

function ArchiveTable({ messages, emptyState, onSelect }: {
  messages: Mail[]; emptyState: React.ReactNode; onSelect: (mail: Mail) => void;
}) {
  return (
    <div className="table-scroll archive-table" tabIndex={0} role="region" aria-label="收件档案列表">
      <table>
        <thead><tr>
          <th scope="col">邮件主题 / 发件人</th>
          <th scope="col">已识别模板</th>
          <th scope="col">附件</th>
          <th scope="col">收件时间</th>
          <th scope="col">处理状态</th>
        </tr></thead>
        <tbody>{emptyState && <tr><td className="archive-empty-cell" colSpan={5}>{emptyState}</td></tr>}
        {messages.map((mail) => (
          <tr key={mail.id}>
            <td><button type="button" className="archive-subject" onClick={() => onSelect(mail)}>{mail.subject || "无主题"}</button>
              <span className="archive-sender">{mail.sender || "未知发件人"}</span>
              <span className="archive-mobile-meta">
                <Status state={mail.state} />
                <time dateTime={mail.received_at}>{formatSyncTime(mail.received_at)}</time>
              </span>
            </td>
            <td>{mail.templates.map((template) => templateLabels[template] ?? template).join("、") || "—"}</td>
            <td>{mail.attachment_count} 个</td>
            <td><time dateTime={mail.received_at}>{chinaTime.format(new Date(mail.received_at))}</time></td>
            <td><Status state={mail.state} /></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function QueueEmpty({ filter, query, sync, mailbox, onViewArchive }: {
  filter: MailFilter; query: string; sync: SyncView; mailbox: MailboxStatus | null | undefined; onViewArchive: () => void;
}) {
  const titles: Partial<Record<MailFilter, string>> = {
    failed: "没有采集或解析异常", review: "没有待人工核对的邮件", queued: "解析队列为空",
  };
  let title = titles[filter] ?? "当前没有待处理邮件";
  let description = "新产生的异常和待核对记录会进入队列。";
  if (sync.kind === "synced" && mailbox?.last_sync_at) {
    description = `上次同步于 ${formatSyncTime(mailbox.last_sync_at)} 完成。`;
  }
  if (sync.kind === "waiting") {
    title = "等待首次同步";
    description = "采集已启动，首批邮件到达后会更新队列。";
  }
  if (query) {
    title = "没有匹配的待处理邮件";
    description = "试试其他关键词，或切换到全部待处理。";
  }
  const Icon = query ? SearchOutlined : sync.kind === "waiting" ? ClockCircleOutlined : CheckOutlined;
  return (
    <div className="queue-empty">
      <span className="empty-mark"><Icon aria-hidden="true" /></span>
      <div><h3>{title}</h3><p>{description}</p></div>
      {!query && <button type="button" className="text-button" onClick={onViewArchive}>查看收件档案 <ArrowRightOutlined aria-hidden="true" /></button>}
    </div>
  );
}

function ArchiveEmpty({ filtered, sync }: { filtered: boolean; sync: SyncView }) {
  let description = "邮件归档后，可以在这里查阅附件和解析记录。";
  if (sync.kind === "setup") description = "业务邮箱接入后，归档邮件会出现在这里。";
  if (sync.kind === "waiting") description = "采集已启动，正在等待首批邮件。";
  return (
    <div className="archive-empty">
      <DatabaseOutlined aria-hidden="true" />
      <h2>{filtered ? "未找到匹配的归档邮件" : "暂无归档邮件"}</h2>
      <p>{filtered ? "调整关键词或处理状态后再试。" : description}</p>
    </div>
  );
}

function MailSearch({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <label className="search">
      <SearchOutlined aria-hidden="true" />
      <input type="search" aria-label="搜索邮件主题、发件人、附件名" placeholder="搜索主题、发件人、附件名"
        value={value} maxLength={120} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

export function IntakeWorkspace({ view, counts, mailbox, sync, revision, refresh, onConfigure, onViewArchive }: {
  view: "queue" | "archive";
  counts: IntakeCounts | undefined;
  mailbox: MailboxStatus | null | undefined;
  sync: SyncView;
  revision: number;
  refresh: () => void;
  onConfigure?: () => void;
  onViewArchive: () => void;
}) {
  const archive = view === "archive";
  const defaultState = archive ? "all" : "actionable";
  const [filter, setFilter] = useState<MailFilter>(defaultState);
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [page, setPage] = useState<MailPage>();
  const [listError, setListError] = useState<{ key: string; message: string }>();
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState("");
  const [selection, setSelection] = useState<Mail>();
  const refreshKey = JSON.stringify([revision, mailbox?.last_sync_at, counts?.total, counts?.failed, counts?.review, counts?.queued]);
  const listKey = JSON.stringify([filter, query]);
  const requestKey = JSON.stringify([listKey, refreshKey]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(search.trim()), 260);
    return () => window.clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    const controller = new AbortController();
    getMailPage(filter, query, controller.signal)
      .then((rows) => {
        if (controller.signal.aborted) return;
        setPage({ key: listKey, fetchedFor: requestKey, rows: rows.slice(0, MAIL_PAGE_SIZE), more: rows.length > MAIL_PAGE_SIZE });
        setListError(undefined);
        setMoreError("");
      })
      .catch((reason) => {
        if (!controller.signal.aborted) setListError({ key: requestKey, message: messageOf(reason) });
      });
    return () => controller.abort();
  }, [filter, query, listKey, requestKey]);

  const current = page?.key === listKey ? page : undefined;
  const error = listError?.key === requestKey ? listError.message : "";
  const waiting = current?.fetchedFor !== requestKey && !error;
  const rows = current?.rows ?? [];
  const selected = archive ? selection : rows.find((mail) => mail.id === selection?.id) ?? rows[0];
  const filtered = query !== "" || filter !== defaultState;
  const onboarding = !archive && !error && !filtered && counts?.total === 0 && ["setup", "test", "stopped"].includes(sync.kind);
  const issue = sync.kind === "failed" || sync.kind === "folder" || sync.tone === "unavailable";

  function chooseFilter(state: MailFilter) {
    setFilter(state);
    setSelection(undefined);
  }

  function updateSearch(value: string) {
    setSearch(value);
    setSelection(undefined);
  }

  async function loadMore() {
    if (!current || loadingMore) return;
    setLoadingMore(true);
    setMoreError("");
    try {
      const more = await getMailPage(filter, query, undefined, current.rows[current.rows.length - 1].id);
      setPage((previous) => previous === current ? {
        ...current, rows: [...current.rows, ...more.slice(0, MAIL_PAGE_SIZE)], more: more.length > MAIL_PAGE_SIZE,
      } : previous);
    } catch (reason) {
      setMoreError(messageOf(reason));
    } finally {
      setLoadingMore(false);
    }
  }

  const pager = current?.more && <div className="workbench-pager">
    {moreError && <p className="notice failed" role="alert">{moreError}</p>}
    <button type="button" className="button" disabled={waiting || loadingMore} onClick={() => void loadMore()}>
      {loadingMore ? "正在加载…" : "加载更多邮件"}
    </button>
  </div>;

  const refreshButton = <button type="button" className="button refresh-button" disabled={waiting} onClick={refresh}>
    <ReloadOutlined aria-hidden="true" /> 刷新
  </button>;
  const errorNotice = error && <p className="notice failed" role="alert">{error}</p>;

  if (archive) {
    const countLabel = !filtered && counts
      ? `已归档 ${counts.total} 封`
      : current && !waiting && !error
        ? `${current.more ? "已显示" : "匹配"} ${rows.length} 封`
        : "";
    const tableMessage = rows.length === 0 && (waiting
      ? <p className="empty" role="status">正在读取归档邮件…</p>
      : error ? <p className="empty">档案读取失败，可刷新后重试。</p> : <ArchiveEmpty filtered={filtered} sync={sync} />);
    return (
      <main className="workbench archive-page">
        <div className="workbench-heading">
          <div className="archive-title"><DatabaseOutlined aria-hidden="true" /><h1>收件档案</h1></div>
          {refreshButton}
        </div>
        {errorNotice}
        {selected ? <>
          <button type="button" className="text-button archive-back" onClick={() => setSelection(undefined)}>
            <ArrowLeftOutlined aria-hidden="true" /> 返回档案
          </button>
          <div className="archive-detail">
            <MailDetail key={selected.id} mail={selected} refreshKey={refreshKey} archive onBack={() => setSelection(undefined)} />
          </div>
        </> : (
          <section className="archive-surface" aria-label="归档邮件查询">
            <div className="archive-toolbar">
              <MailSearch value={search} onChange={updateSearch} />
              <label className="archive-filter">
                <span>处理状态</span>
                <select value={filter} onChange={(event) => chooseFilter(event.target.value as MailFilter)}>
                  {archiveFilters.map(({ id, label }) => <option key={id} value={id}>{label}</option>)}
                </select>
              </label>
              <span className="archive-count" role="status">{countLabel}</span>
            </div>
            <ArchiveTable messages={rows} emptyState={tableMessage} onSelect={setSelection} />
            {pager}
          </section>
        )}
      </main>
    );
  }

  const activeQueue = queueFilters.find(({ id }) => id === filter);
  const queueHeading = <div className="queue-list-header">
    <div className="queue-list-heading">
      <h2>{activeQueue?.label ?? "全部待处理"}</h2>
      {activeQueue ? (
        <button type="button" className="text-button" onClick={() => chooseFilter("actionable")}>查看全部</button>
      ) : current && rows.length > 0 && <span>已显示 {rows.length} 封</span>}
    </div>
    <MailSearch value={search} onChange={updateSearch} />
  </div>;

  return (
    <main className="workbench queue-page">
      <div className="workbench-heading">
        <div><h1>待处理</h1><p>检查采集异常，核对交付附件。</p></div>
        {refreshButton}
      </div>
      {issue && <div className="notice failed sync-notice" role="status">
        <span>{sync.kind === "folder" ? "文件夹标识发生变化，采集已暂停。" : sync.kind === "failed" ? "上次邮箱同步失败，可继续查看已归档邮件。" : "暂时无法读取邮箱同步状态。"}</span>
        {onConfigure && sync.kind !== "unknown" && <button type="button" className="text-button" onClick={onConfigure}>
          查看邮箱配置 <ArrowRightOutlined aria-hidden="true" />
        </button>
        }
      </div>}
      {errorNotice}

      {onboarding ? <ConnectMailbox sync={sync} onConfigure={onConfigure} /> : (
        <>
          <div className="queue-summary" role="group" aria-label="待处理状态">
            {queueFilters.map(({ id, label, icon: Icon }) => (
              <button type="button" className={`queue-metric ${id}`} key={id} aria-pressed={filter === id}
                onClick={() => chooseFilter(filter === id ? "actionable" : id)}>
                <span className="queue-metric-icon"><Icon aria-hidden="true" /></span>
                <span>{label}</span>
                <strong>{counts?.[id] ?? "—"}</strong>
              </button>
            ))}
          </div>
          <div className={`workspace queue-layout ${selected ? (selection ? "detail-open" : "") : "queue-idle"}`}>
            <aside className="mail-list" aria-label="待处理邮件">
              {queueHeading}
              <div className="mail-items">
                {rows.map((mail) => (
                  <button type="button" key={mail.id} className={`mail-item ${mail.id === selected?.id ? "selected" : ""}`}
                    aria-pressed={mail.id === selected?.id} onClick={() => setSelection(mail)}>
                    <span className="mail-subject">{mail.subject || "无主题"}</span>
                    <span className="mail-sender">{mail.sender || "未知发件人"}</span>
                    <span className="mail-item-bottom">
                      <Status state={mail.state} />
                      <time dateTime={mail.received_at}>{formatSyncTime(mail.received_at)}</time>
                    </span>
                  </button>
                ))}
                {pager}
                {!selected && waiting && <p className="empty" role="status">正在读取待处理邮件…</p>}
                {!selected && current && !waiting && !error && <QueueEmpty filter={filter} query={query} sync={sync}
                  mailbox={mailbox} onViewArchive={onViewArchive} />}
              </div>
            </aside>
            {selected && <MailDetail key={selected.id} mail={selected} refreshKey={refreshKey} archive={false}
              onBack={() => setSelection(undefined)} />}
          </div>
        </>
      )}
    </main>
  );
}
