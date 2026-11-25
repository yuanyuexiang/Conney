"use client";

import {
  ArrowRightOutlined, ExclamationCircleOutlined, FileDoneOutlined,
  FileExcelOutlined, FileSearchOutlined, InboxOutlined, MailOutlined, ReloadOutlined,
} from "@ant-design/icons";
import { type ReactNode, useEffect, useState } from "react";
import { Status, type Mail, type QueueFilter } from "./intake-workspace";
import { messageOf, relayRequest } from "./relay-api";
import { WorkbenchHeader, type WorkbenchHeading } from "./workbench-header";
import { formatSyncTime, type IntakeCounts, type SyncView } from "./workbench-status";

type Receipt = { date: string; received: number };
type DashboardData = {
  checked_at: string;
  receipts: Receipt[];
  parsed_today: number;
  delivery_drafts: number;
  drafts_for_review: number;
  recent_mail: Mail[];
};

const days = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", month: "long", day: "numeric", weekday: "long",
});
const numbers = new Intl.NumberFormat("zh-CN");

function Metric({ label, value, unit, icon, tone, hint, onClick }: {
  label: string;
  value?: number;
  unit: string;
  icon: ReactNode;
  tone: "blue" | "green" | "amber" | "slate";
  hint?: string;
  onClick: () => void;
}) {
  return (
    <button className="dashboard-metric" type="button" onClick={onClick}>
      <span className="dashboard-metric-heading">
        <span className={`dashboard-icon ${tone}`}>{icon}</span>
        <span>{label}</span>
        <ArrowRightOutlined className="dashboard-action-arrow" aria-hidden="true" />
      </span>
      <span className="dashboard-metric-value">
        <strong>{value === undefined ? "—" : numbers.format(value)}</strong>
        <span>{unit}</span>
      </span>
      {hint && <span className="dashboard-metric-hint">{hint}</span>}
    </button>
  );
}

function ReceiptTrend({ receipts, loading }: { receipts?: Receipt[]; loading: boolean }) {
  const maximum = Math.max(1, ...(receipts ?? []).map(({ received }) => received));
  return (
    <section className="dashboard-panel" aria-labelledby="receipts-heading">
      <div className="dashboard-panel-heading">
        <h2 id="receipts-heading">最近 7 天收件</h2>
        <span>封 · 北京时间</span>
      </div>
      {receipts ? (
        <ol className="dashboard-chart" aria-label="每日收件数量">
          {receipts.map(({ date, received }, index) => {
            const [, month, day] = date.split("-");
            return (
              <li key={date} className={index === receipts.length - 1 ? "dashboard-chart-today" : undefined}>
                <div className="dashboard-bar-area">
                  <strong>{numbers.format(received)}</strong>
                  <span className="dashboard-bar" aria-hidden="true" style={{ height: `${received / maximum * 100}%` }} />
                </div>
                <time dateTime={date}>{Number(month)}/{Number(day)}</time>
              </li>
            );
          })}
        </ol>
      ) : <p className="dashboard-empty">{loading ? "正在读取收件趋势…" : "收件趋势暂不可用"}</p>}
    </section>
  );
}

function FollowUp({ counts, drafts, sync, onQueue, onReview, onConfigure }: {
  counts?: IntakeCounts;
  drafts?: number;
  sync: SyncView;
  onQueue: (filter?: QueueFilter) => void;
  onReview: () => void;
  onConfigure?: () => void;
}) {
  const tasks = [
    { label: "采集与解析异常", icon: ExclamationCircleOutlined, count: counts?.failed, tone: "red", action: () => onQueue("failed") },
    { label: "邮件待人工核对", icon: FileSearchOutlined, count: counts?.review, tone: "amber", action: () => onQueue("review") },
    { label: "交付草稿待核对", icon: FileExcelOutlined, count: drafts, tone: "blue", action: onReview },
  ];
  const configure = onConfigure && ["setup", "test", "stopped", "folder", "failed"].includes(sync.kind);

  return (
    <section className="dashboard-panel dashboard-follow-up" aria-labelledby="follow-up-heading">
      <div className="dashboard-panel-heading"><h2 id="follow-up-heading">待跟进</h2></div>
      <ul>
        {tasks.map(({ label, icon: Icon, count, tone, action }) => (
          <li key={label}>
            <button type="button" className="dashboard-task" onClick={action}>
              <span className={`dashboard-icon ${tone}`}><Icon aria-hidden="true" /></span>
              <span>{label}</span>
              <strong className={count ? `dashboard-task-count ${tone}` : undefined}>{count === undefined ? "—" : numbers.format(count)}</strong>
              <ArrowRightOutlined className="dashboard-action-arrow" aria-hidden="true" />
            </button>
          </li>
        ))}
      </ul>
      {configure && <button className="text-button dashboard-mailbox-link" type="button" onClick={onConfigure}>
        <MailOutlined aria-hidden="true" />{sync.label}<ArrowRightOutlined aria-hidden="true" />
      </button>}
    </section>
  );
}

export function Dashboard({ heading, counts, sync, syncAt, revision, onRefresh, onArchive, onMail, onQueue,
  onDelivery, onReview, onConfigure }: {
  heading: WorkbenchHeading;
  counts?: IntakeCounts;
  sync: SyncView;
  syncAt?: string | null;
  revision: number;
  onRefresh: () => void;
  onArchive: () => void;
  onMail: (id: number) => void;
  onQueue: (filter?: QueueFilter) => void;
  onDelivery: () => void;
  onReview: () => void;
  onConfigure?: () => void;
}) {
  const [data, setData] = useState<DashboardData>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function read() {
      if (pending) return;
      pending = true;
      setLoading(true);
      try {
        const result = await relayRequest<DashboardData>("/dashboard", { signal: controller.signal });
        if (!controller.signal.aborted) {
          setData(result);
          setError("");
        }
      } catch (reason) {
        if (!controller.signal.aborted) setError(messageOf(reason));
      } finally {
        pending = false;
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    void read();
    const interval = window.setInterval(() => {
      if (!document.hidden) void read();
    }, 30000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [revision, syncAt]);

  const received = data?.receipts.at(-1)?.received;
  const totalReceipts = data?.receipts.reduce((total, day) => total + day.received, 0);
  const actionable = counts ? counts.failed + counts.review + counts.queued : undefined;
  return (
    <main className="workbench">
      <WorkbenchHeader {...heading}
        meta={data && <time dateTime={data.checked_at}>{days.format(new Date(data.checked_at))}</time>}
        actions={<button type="button" className="button" onClick={onRefresh} disabled={loading}>
          <ReloadOutlined spin={loading} aria-hidden="true" /> 刷新数据
        </button>} />
      <div className="dashboard">
        {error && <p className="notice failed" role="alert">{error}</p>}
        {loading && !data && <span className="dashboard-sr-only" role="status">正在读取仪表盘数据</span>}
        <section className="dashboard-metrics" aria-label="业务指标">
          <Metric label="今日收件" value={received} unit="封" tone="blue" icon={<MailOutlined aria-hidden="true" />}
            hint={totalReceipts === undefined ? undefined : `近 7 天共 ${numbers.format(totalReceipts)} 封`} onClick={onArchive} />
          <Metric label="今日解析完成" value={data?.parsed_today} unit="份" tone="green" icon={<FileDoneOutlined aria-hidden="true" />}
            hint="按附件解析完成时间" onClick={onArchive} />
          <Metric label="待处理邮件" value={actionable} unit="封" tone="amber" icon={<InboxOutlined aria-hidden="true" />}
            hint={counts ? `其中 ${numbers.format(counts.queued)} 封在解析队列` : undefined} onClick={() => onQueue()} />
          <Metric label="交付草稿" value={data?.delivery_drafts} unit="批" tone="slate" icon={<FileExcelOutlined aria-hidden="true" />}
            hint={data && `${numbers.format(data.drafts_for_review)} 批待核对`} onClick={onDelivery} />
        </section>
        <div className="dashboard-overview">
          <ReceiptTrend receipts={data?.receipts} loading={loading} />
          <FollowUp counts={counts} drafts={data?.drafts_for_review} sync={sync} onQueue={onQueue}
            onReview={onReview} onConfigure={onConfigure} />
        </div>
        <section className="dashboard-panel" aria-labelledby="recent-mail-heading">
          <div className="dashboard-panel-heading">
            <h2 id="recent-mail-heading">最近收件</h2>
            <button className="text-button dashboard-archive-link" type="button" onClick={onArchive}>
              收件档案 <ArrowRightOutlined aria-hidden="true" />
            </button>
          </div>
          {data?.recent_mail.length ? (
            <ul className="dashboard-recent">
              {data.recent_mail.map((mail) => (
                <li key={mail.id}>
                  <button type="button" onClick={() => onMail(mail.id)} className="dashboard-mail">
                    <MailOutlined aria-hidden="true" />
                    <span className="dashboard-mail-subject">{mail.subject || "无主题"}</span>
                    <Status state={mail.state} />
                    <time dateTime={mail.received_at}>{formatSyncTime(mail.received_at)}</time>
                    <ArrowRightOutlined className="dashboard-action-arrow" aria-hidden="true" />
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            <p className="dashboard-empty">
              {loading && !data ? "正在读取邮件…" : data ? "暂无收件记录" : "最近收件暂不可用"}
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
