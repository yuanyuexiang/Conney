"use client";

import {
  ApiOutlined, ArrowRightOutlined, CloudDownloadOutlined, DatabaseOutlined, FileExcelOutlined,
  HddOutlined, MailOutlined, ReloadOutlined, UnorderedListOutlined,
} from "@ant-design/icons";
import { useEffect, useState, type ReactNode } from "react";
import { relayRequest } from "./relay-api";
import { describeSync, type MailboxStatus } from "./workbench-status";

type Tone = "ready" | "checking" | "attention" | "unavailable";
type Badge = { tone: Tone; label: string };
type Worker = { state: "reporting" | "stale" | "unseen"; last_seen_at: string | null };
type Parsing = {
  pending: number;
  processing: number;
  expired: number;
  failed: number;
  succeeded: number;
  last_success_at: string | null;
};
type Operations = {
  checked_at: string;
  mail_worker: Worker;
  parser_worker: Worker;
  mailbox: (MailboxStatus & { poll_minutes: number }) | null;
  parsing: Parsing;
  archive: { state: "empty" | "verified" | "unavailable" | "damaged" };
};
type Snapshot = {
  api: boolean;
  database: boolean | null;
  operations: Operations | null;
  checkedAt: string;
};

const unread: Badge = { tone: "checking", label: "未取得状态" };
const checkTime = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai", dateStyle: "short", timeStyle: "medium",
});
const workerBadges: Record<Worker["state"], Badge> = {
  reporting: { tone: "ready", label: "心跳正常" },
  stale: { tone: "unavailable", label: "心跳超时" },
  unseen: { tone: "attention", label: "未收到心跳" },
};
const archiveViews: Record<Operations["archive"]["state"], Badge & { detail: string }> = {
  empty: { tone: "checking", label: "等待首份附件", detail: "归档目录可读取。" },
  verified: { tone: "ready", label: "原件校验通过", detail: "已核验最近一份归档原件的大小及 SHA-256。" },
  unavailable: { tone: "unavailable", label: "归档读取失败", detail: "请检查附件存储卷和读取权限。" },
  damaged: { tone: "unavailable", label: "原件校验失败", detail: "最近一份归档原件缺失或校验不一致。" },
};

async function probe(service: "live" | "ready", signal: AbortSignal): Promise<boolean | null> {
  const response = await fetch(`/api/v1/health/${service}`, { cache: "no-store", signal });
  if (response.status !== 200 && response.status !== 503) return null;
  const data: { status?: string } = await response.json();
  if (response.status === 200 && data.status === "ok") return true;
  if (response.status === 503 && data.status === "unavailable") return false;
  return null;
}

function Signal({ tone, label }: Badge) {
  return <span className={`connection operation-signal ${tone}`}><span className="connection-dot" />{label}</span>;
}

function Timestamp({ value }: { value: string }) {
  return <time dateTime={value}>{checkTime.format(new Date(value))}</time>;
}

function StatusCard({ title, icon, badge, children }: {
  title: string; icon: ReactNode; badge: Badge; children: ReactNode;
}) {
  return (
    <article className="operation-card">
      <div className="operation-card-heading">
        <span className="operation-icon">{icon}</span>
        <h3>{title}</h3>
      </div>
      <Signal {...badge} />
      <div className="operation-detail">{children}</div>
    </article>
  );
}

function heartbeatDetail(worker: Worker | undefined) {
  return worker?.last_seen_at
    ? <>最近心跳 <Timestamp value={worker.last_seen_at} /></>
    : worker ? "等待进程上报运行状态。" : "等待运行监测数据。";
}

function parsingBadge(parsing: Parsing | undefined): Badge {
  if (!parsing) return unread;
  if (parsing.expired > 0) return { tone: "unavailable", label: "有超时任务" };
  if (parsing.failed > 0) return { tone: "attention", label: "有失败记录" };
  if (parsing.pending + parsing.processing > 0) return { tone: "checking", label: "解析处理中" };
  return { tone: "ready", label: "队列空闲" };
}

export function SystemStatus({ onConfigure, onViewQueue }: {
  onConfigure?: () => void; onViewQueue: () => void;
}) {
  const [snapshot, setSnapshot] = useState<Snapshot>();
  const [checking, setChecking] = useState(true);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let inFlight = false;
    async function readStatus() {
      if (inFlight) return;
      inFlight = true;
      setChecking(true);
      const signal = AbortSignal.any([controller.signal, AbortSignal.timeout(8000)]);
      const [live, ready, report] = await Promise.allSettled([
        probe("live", signal),
        probe("ready", signal),
        relayRequest<Operations>("/health/operations", { signal }),
      ]);
      inFlight = false;
      if (controller.signal.aborted) return;
      setSnapshot({
        api: live.status === "fulfilled" && live.value === true,
        database: ready.status === "fulfilled" ? ready.value : null,
        operations: report.status === "fulfilled" ? report.value : null,
        checkedAt: new Date().toISOString(),
      });
      setChecking(false);
    }
    void readStatus();
    const interval = window.setInterval(() => {
      if (!document.hidden) void readStatus();
    }, 30000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, [refresh]);

  const report = snapshot?.operations;
  const mailbox = report?.mailbox;
  const mailboxSync = describeSync(mailbox, !report);
  const isOverdue = report && mailboxSync.kind === "synced" && mailbox?.last_sync_at
    && Date.parse(report.checked_at) - Date.parse(mailbox.last_sync_at) > (mailbox.poll_minutes * 60 + 120) * 1000;
  const mailboxBadge: Badge = !report ? unread : isOverdue
    ? { tone: "attention", label: "同步记录已超轮询周期" }
    : mailboxSync;
  const apiBadge: Badge = !snapshot ? unread : snapshot.api
    ? { tone: "ready", label: "可响应" } : { tone: "unavailable", label: "访问失败" };
  const databaseBadge: Badge = snapshot?.database === true ? { tone: "ready", label: "连接正常" }
    : snapshot?.database === false ? { tone: "unavailable", label: "未就绪" } : unread;
  const archive = report && archiveViews[report.archive.state];
  const parsing = report?.parsing;
  const mailWorkerBadge = report ? workerBadges[report.mail_worker.state] : unread;
  const parserWorkerBadge = report ? workerBadges[report.parser_worker.state] : unread;
  const workloadBadge = parsingBadge(parsing);
  const signals = [apiBadge, databaseBadge, mailWorkerBadge, parserWorkerBadge, mailboxBadge, workloadBadge, archive ?? unread];

  const summary: Badge = !snapshot
    ? { tone: "checking", label: "正在检查系统" }
    : signals.some(({ tone }) => tone === "unavailable") || !report || snapshot.database === null
      ? { tone: "unavailable", label: "部分运行检查异常" }
      : signals.some(({ tone }) => tone === "attention") || mailboxSync.kind !== "synced"
        ? { tone: "attention", label: "有项目待关注" }
        : { tone: "ready", label: "运行检查通过" };

  return (
    <main className="workbench operations-page">
      <div className="workbench-heading">
        <div><h1>系统状态</h1><p>查看从邮件采集到附件归档和解析的运行情况。</p></div>
        <button type="button" className="button refresh-button" disabled={checking}
          onClick={() => setRefresh((value) => value + 1)}>
          <ReloadOutlined aria-hidden="true" /> {checking ? "检查中…" : "重新检查"}
        </button>
      </div>

      <div className="operation-summary" role="status">
        <Signal {...summary} />
        {snapshot && <span className="operation-checked-at">最近检查 <Timestamp value={snapshot.checkedAt} /></span>}
      </div>
      {snapshot && !report && <p className="notice failed" role="alert">运行监测数据暂不可用，请先查看 API 服务和数据库状态。</p>}

      <section className="operation-group" aria-labelledby="service-health">
        <h2 id="service-health">运行组件</h2>
        <div className="operation-grid component-grid">
          <StatusCard title="API 服务" icon={<ApiOutlined aria-hidden="true" />} badge={apiBadge}>
            通过当前入口检查 API 响应。
          </StatusCard>
          <StatusCard title="数据库" icon={<DatabaseOutlined aria-hidden="true" />} badge={databaseBadge}>
            检查 PostgreSQL 连接和迁移基线。
          </StatusCard>
          <StatusCard title="采集进程" icon={<CloudDownloadOutlined aria-hidden="true" />} badge={mailWorkerBadge}>
            {heartbeatDetail(report?.mail_worker)}
          </StatusCard>
          <StatusCard title="解析进程" icon={<FileExcelOutlined aria-hidden="true" />} badge={parserWorkerBadge}>
            {heartbeatDetail(report?.parser_worker)}
          </StatusCard>
        </div>
      </section>

      <section className="operation-group" aria-labelledby="workflow-health">
        <h2 id="workflow-health">业务链路</h2>
        <div className="operation-grid workflow-grid">
          <StatusCard title="263 邮箱同步" icon={<MailOutlined aria-hidden="true" />} badge={mailboxBadge}>
            {mailbox && <dl className="operation-facts">
              <div><dt>轮询间隔</dt><dd>{mailbox.poll_minutes} 分钟</dd></div>
              <div><dt>最近一次同步</dt><dd>{mailbox.last_sync_at ? <Timestamp value={mailbox.last_sync_at} /> : "暂无记录"}</dd></div>
              {mailbox.last_sync_count !== null && <div><dt>最近一批处理</dt><dd>{mailbox.last_sync_count} 封邮件</dd></div>}
            </dl>}
            {report && !mailbox && <p>配置业务邮箱后可启动只读采集。</p>}
            {onConfigure && <button type="button" className="text-button operation-link" onClick={onConfigure}>
              查看邮箱配置 <ArrowRightOutlined aria-hidden="true" />
            </button>}
          </StatusCard>
          <StatusCard title="附件归档" icon={<HddOutlined aria-hidden="true" />} badge={archive ?? unread}>
            {archive?.detail ?? "等待运行监测数据。"}
          </StatusCard>
          <StatusCard title="解析任务" icon={<UnorderedListOutlined aria-hidden="true" />} badge={workloadBadge}>
            {parsing && <>
              <dl className="operation-counters">
                <div><dt>排队</dt><dd>{parsing.pending}</dd></div>
                <div><dt>处理中</dt><dd>{parsing.processing}</dd></div>
                <div><dt>租约超时</dt><dd>{parsing.expired}</dd></div>
                <div><dt>失败</dt><dd>{parsing.failed}</dd></div>
                <div><dt>成功</dt><dd>{parsing.succeeded}</dd></div>
              </dl>
              <p>最近解析成功 {parsing.last_success_at ? <Timestamp value={parsing.last_success_at} /> : "暂无记录"}</p>
            </>}
            <button type="button" className="text-button operation-link" onClick={onViewQueue}>
              查看待处理 <ArrowRightOutlined aria-hidden="true" />
            </button>
          </StatusCard>
        </div>
      </section>
    </main>
  );
}
