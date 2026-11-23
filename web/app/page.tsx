"use client";

import {
  AppstoreOutlined,
  CalendarOutlined,
  CloudServerOutlined,
  DownloadOutlined,
  ExclamationCircleOutlined,
  FileExcelOutlined,
  FileTextOutlined,
  InboxOutlined,
  MailOutlined,
  ReloadOutlined,
  SearchOutlined,
  TableOutlined,
  TeamOutlined,
  KeyOutlined,
} from "@ant-design/icons";
import Image from "next/image";
import { useEffect, useState } from "react";
import { AccountLayout, PasswordForm, SignIn } from "./account-forms";
import { messageOf, relayRequest, RelayRequestError, roleLabels, type User } from "./relay-api";
import { UserManagement } from "./user-management";
import { IntakeWorkspace } from "./intake-workspace";
import { MailboxSettings } from "./mailbox-settings";
import {
  demoMail,
  statusLabels,
  type MailRecord,
  type MailStatus,
} from "./workspace-data";

const sections = [
  { id: "intake", label: "收件记录", icon: FileTextOutlined },
  { id: "overview", label: "工作概览", icon: AppstoreOutlined },
  { id: "mail", label: "邮件与附件", icon: InboxOutlined },
  { id: "plans", label: "交付计划", icon: TableOutlined },
  { id: "issues", label: "异常任务", icon: ExclamationCircleOutlined },
  { id: "mailbox", label: "邮箱配置", icon: MailOutlined },
  { id: "users", label: "用户管理", icon: TeamOutlined },
  { id: "password", label: "修改密码", icon: KeyOutlined },
  { id: "system", label: "系统状态", icon: CloudServerOutlined },
] as const;
type Section = (typeof sections)[number]["id"];
type Filter = "all" | MailStatus;
type DetailTab = "files" | "parsed";
type Connection = "checking" | "ready" | "unavailable";

const filters: { id: Filter; label: string }[] = [
  { id: "all", label: "全部" },
  { id: "complete", label: "已解析" },
  { id: "review", label: "待核对" },
  { id: "queued", label: "待处理" },
  { id: "failed", label: "失败" },
];

const connectionLabels: Record<Connection, string> = {
  checking: "检查中",
  ready: "服务就绪",
  unavailable: "服务暂不可用",
};

function Status({ status }: { status: MailStatus }) {
  return <span className={`status ${status}`}>{statusLabels[status]}</span>;
}

function PlanTable({ records, includeTemplate = false }: { records: MailRecord[]; includeTemplate?: boolean }) {
  const rows = records.flatMap((mail) => mail.rows.map((row) => ({ ...row, mail })));
  if (rows.length === 0) {
    return <p className="empty small-empty">暂无可显示的解析记录</p>;
  }
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            {includeTemplate && <th scope="col">报文模板</th>}
            <th scope="col">物料编号</th>
            <th scope="col">交付日期 / 区间</th>
            <th className="numeric" scope="col">原始数量</th>
            <th scope="col">单位</th>
            <th scope="col">来源坐标</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(({ mail, material, period, quantity, unit, source }) => (
            <tr key={`${mail.id}-${source}`}>
              {includeTemplate && <td>{mail.template}</td>}
              <td className="mono">{material}</td>
              <td>{period}</td>
              <td className="numeric mono">{quantity}</td>
              <td>{unit}</td>
              <td className="source mono">{source}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Brand() {
  return (
    <div className="brand">
      <Image src="/brand/knn-logo.png" width={152} height={38} alt="康尼新能源" unoptimized priority />
      <span className="brand-divider" />
      <span className="product-name">EDI 交付计划管理</span>
    </div>
  );
}

export default function Home() {
  const [user, setUser] = useState<User | null>();
  const [logoutError, setLogoutError] = useState("");
  const [leaving, setLeaving] = useState(false);
  const [sessionError, setSessionError] = useState("");
  const [sessionCheck, setSessionCheck] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<User>("/auth/me", { signal: controller.signal })
      .then(setUser)
      .catch((error) => {
        if (!controller.signal.aborted) {
          if (error instanceof RelayRequestError && error.status === 401) setUser(null);
          else setSessionError(messageOf(error));
        }
      });
    function expire() { setUser(null); }
    window.addEventListener("relay-session-expired", expire);
    return () => {
      controller.abort();
      window.removeEventListener("relay-session-expired", expire);
    };
  }, [sessionCheck]);

  async function logout() {
    setLeaving(true);
    setLogoutError("");
    try {
      await relayRequest("/auth/logout", { method: "POST" });
      setUser(null);
    } catch (error) {
      setLogoutError(messageOf(error));
    } finally {
      setLeaving(false);
    }
  }

  const exit = <button type="button" className="button" disabled={leaving} onClick={() => void logout()}>退出登录</button>;
  if (user?.must_change_password) return (
    <AccountLayout action={exit}>
      <h1>修改临时密码</h1>
      <PasswordForm required onUpdated={setUser} />
      {logoutError && <p role="alert" className="notice failed">{logoutError}</p>}
    </AccountLayout>
  );

  if (user === null) return <SignIn onSignedIn={setUser} />;

  if (!user) return (
    <AccountLayout>
      <h1>登录工作台</h1>
      {sessionError ? (
        <>
          <p role="alert" className="notice failed">{sessionError}</p>
          <button type="button" className="button" onClick={() => { setSessionError(""); setSessionCheck((value) => value + 1); }}>重试</button>
        </>
      ) : <p className="auth-instructions" role="status">正在检查登录状态…</p>}
    </AccountLayout>
  );

  return <Workspace user={user} onUpdated={setUser} exit={exit} logoutError={logoutError} />;
}

function Workspace({ user, onUpdated, exit, logoutError }: {
  user: User;
  onUpdated: (user: User) => void;
  exit: React.ReactNode;
  logoutError: string;
}) {
  const [section, setSection] = useState<Section>("intake");
  const [records, setRecords] = useState(demoMail);
  const [selectedId, setSelectedId] = useState(demoMail[0].id);
  const [filter, setFilter] = useState<Filter>("all");
  const [query, setQuery] = useState("");
  const [tab, setTab] = useState<DetailTab>("files");
  const [mobileDetail, setMobileDetail] = useState(false);
  const [connection, setConnection] = useState<Connection>("checking");
  const [check, setCheck] = useState(0);
  const [retryId, setRetryId] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/health/ready", { cache: "no-store", signal: controller.signal })
      .then((response) => setConnection(response.ok ? "ready" : "unavailable"))
      .catch(() => {
        if (!controller.signal.aborted) setConnection("unavailable");
      });
    return () => controller.abort();
  }, [check]);

  const issueCount = records.filter((mail) => mail.status === "failed" || mail.status === "review").length;
  const workingRecords = section === "issues"
    ? records.filter((mail) => mail.status === "failed" || mail.status === "review" || mail.id === retryId)
    : records;
  const normalizedQuery = query.trim().toLowerCase();
  const visible = workingRecords.filter((mail) =>
    (filter === "all" || filter === mail.status)
    && [mail.subject, mail.sender, mail.template, mail.filename].some(
      (value) => value.toLowerCase().includes(normalizedQuery),
    ),
  );
  const selected = visible.find((mail) => mail.id === selectedId) ?? visible[0];
  const showWorkspace = section === "mail" || section === "issues";
  const allowedSections = sections.filter(({ id }) => user.role === "admin" || (id !== "users" && id !== "mailbox"));
  const activeSection = allowedSections.find(({ id }) => id === section) ?? allowedSections[0];
  const demoSection = section === "overview" || section === "mail" || section === "plans" || section === "issues";

  function navigate(id: Section) {
    setSection(id);
    setFilter("all");
    setQuery("");
    setTab("files");
    setMobileDetail(false);
  }

  function selectMail(id: string) {
    setSelectedId(id);
    setTab("files");
    setMobileDetail(true);
  }

  function queueDemoRetry(id: string) {
    setRetryId(id);
    setSelectedId(id);
    setRecords((previous) => previous.map((mail) =>
      mail.id === id ? { ...mail, status: "queued", note: "已在此预览中标记为待重试。" } : mail,
    ));
    setFilter("all");
  }

  function recheck() {
    setConnection("checking");
    setCheck((previous) => previous + 1);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-meta">
          {demoSection && <span className="demo-label" title="邮件、附件及交付记录为演示数据">演示数据</span>}
          <span className={`connection ${connection}`} role="status">
            <span className="connection-dot" />{connectionLabels[connection]}
          </span>
          <span className="account-identity">{user.username} · {roleLabels[user.role]}</span>
          {exit}
        </div>
      </header>
      {logoutError && <p className="notice failed" role="alert">{logoutError}</p>}

      <div className="body-layout">
        <nav className="sidebar" aria-label="主导航">
          <div className="sidebar-label">工作台</div>
          {allowedSections.map(({ id, label, icon: Icon }) => (
            <button
              key={id}
              type="button"
              className={`nav-item ${id === section ? "active" : ""}`}
              aria-current={id === section ? "page" : undefined}
              onClick={() => navigate(id)}
            >
              <Icon aria-hidden="true" />
              <span>{label}</span>
              {id === "issues" && issueCount > 0 && <span className="nav-count">{issueCount}</span>}
            </button>
          ))}
          <span className="sidebar-footer">Relay <span>·</span> 康尼新能源</span>
        </nav>

        {section === "intake" ? <IntakeWorkspace /> : showWorkspace ? (
          <main className={`workspace ${mobileDetail && selected ? "detail-open" : ""}`}>
            <aside className="mail-list" aria-label={`${activeSection.label}列表`}>
              <div className="list-heading">
                <div className="heading-row">
                  <h1>{activeSection.label}</h1>
                  <span className="list-count">{visible.length} 封</span>
                </div>
                <label className="search">
                  <SearchOutlined aria-hidden="true" />
                  <input
                    type="search"
                    aria-label="搜索主题、发件人或模板"
                    placeholder="搜索主题、发件人或模板"
                    value={query}
                    onChange={(event) => { setQuery(event.target.value); setTab("files"); }}
                  />
                </label>
                <div className="filters" aria-label="处理状态筛选">
                  {filters.map(({ id, label }) => (
                    <button key={id} type="button" aria-pressed={filter === id} onClick={() => { setFilter(id); setTab("files"); }}>
                      {label}
                    </button>
                  ))}
                </div>
              </div>

              <div className="mail-items">
                {visible.map((mail) => (
                  <button
                    key={mail.id}
                    className={`mail-item ${selected?.id === mail.id ? "selected" : ""}`}
                    type="button"
                    aria-pressed={selected?.id === mail.id}
                    onClick={() => selectMail(mail.id)}
                  >
                    <span className="mail-item-top">
                      <span className="mail-sender">{mail.sender}</span>
                      <time>{mail.received}</time>
                    </span>
                    <span className="mail-subject">{mail.subject}</span>
                    <span className="mail-item-bottom">
                      <span className="attachment-count"><FileExcelOutlined aria-hidden="true" /> 1 个附件</span>
                      <Status status={mail.status} />
                    </span>
                  </button>
                ))}
                {visible.length === 0 && <p className="empty">没有符合条件的邮件</p>}
              </div>
            </aside>

            <section className="mail-detail" aria-label="邮件详情">
              {selected ? (
                <>
                  <div className="detail-header">
                    <button type="button" className="back-button" onClick={() => setMobileDetail(false)}>返回列表</button>
                    <div className="detail-title">
                      <h2>{selected.subject}</h2>
                      <Status status={selected.status} />
                    </div>
                    <div className="mail-meta">
                      <span>{selected.sender} <span className="sender-address">&lt;{selected.address}&gt;</span></span>
                      <span><CalendarOutlined aria-hidden="true" /> 2026-{selected.received}</span>
                    </div>
                    <div className="detail-tabs" aria-label="邮件详情视图">
                      <button type="button" aria-pressed={tab === "files"} onClick={() => setTab("files")}>附件 <span>1</span></button>
                      <button type="button" aria-pressed={tab === "parsed"} onClick={() => setTab("parsed")}>解析结果</button>
                    </div>
                  </div>
                  <div className="detail-content">
                    {selected.note && (
                      <div className={`notice ${selected.status}`} role="status">
                        <ExclamationCircleOutlined aria-hidden="true" />
                        <span>{selected.note}</span>
                        {selected.status === "failed" && (
                          <button type="button" className="button subtle" onClick={() => queueDemoRetry(selected.id)}>
                            <ReloadOutlined aria-hidden="true" /> 演示重试
                          </button>
                        )}
                      </div>
                    )}
                    {tab === "files" ? (
                      <>
                        <div className="section-heading"><h3>邮件附件</h3><span>1 个 Excel 文件</span></div>
                        <div className="attachment-card">
                          <div className="file-icon"><FileExcelOutlined aria-hidden="true" /></div>
                          <div className="file-info">
                            <div className="filename">{selected.filename}</div>
                            <div className="file-meta">{selected.size} <span>·</span> {selected.template}</div>
                          </div>
                          <a className="button" href={`/demo/${selected.filename}`} download>
                            <DownloadOutlined aria-hidden="true" /> 下载样本
                          </a>
                        </div>
                      </>
                    ) : (
                      <div className="parse-summary">
                        <FileTextOutlined aria-hidden="true" />
                        <span>{selected.template}</span>
                        <span>{new Set(selected.rows.map((row) => row.material)).size} 个物料 · {selected.rows.length} 条记录</span>
                      </div>
                    )}
                    <section className="record-preview" aria-label="原始数量记录">
                      <div className="section-heading">
                        <h3>原始数量记录</h3>
                        {tab === "files" && selected.rows.length > 0 && (
                          <button type="button" className="text-button" onClick={() => setTab("parsed")}>查看解析结果</button>
                        )}
                      </div>
                      <PlanTable records={[selected]} />
                    </section>
                  </div>
                </>
              ) : <div className="detail-empty"><InboxOutlined aria-hidden="true" /><p>请选择一封邮件查看详情</p></div>}
            </section>
          </main>
        ) : (
          <main className="full-page">
            <h1>{activeSection.label}</h1>
            {section === "overview" && (
              <div className="metrics">
                {[
                  { title: "接收邮件", value: records.length, view: "mail" as const },
                  { title: "解析完成", value: records.filter((mail) => mail.status === "complete").length, view: "plans" as const },
                  { title: "异常及待核对", value: issueCount, view: "issues" as const },
                ].map(({ title, value, view }) => (
                  <button type="button" className="metric" key={view} onClick={() => navigate(view)}>
                    <span>{title}</span><strong>{value}</strong>
                  </button>
                ))}
              </div>
            )}
            {section === "plans" && (
              <section className="record-preview page-card" aria-label="原始数量记录">
                <div className="section-heading"><h3>原始数量记录</h3><span>演示预览</span></div>
                <PlanTable records={records} includeTemplate />
              </section>
            )}
            {section === "mailbox" && user.role === "admin" && <MailboxSettings />}
            {section === "users" && user.role === "admin" && <UserManagement current={user} />}
            {section === "password" && <div className="page-card mailbox-card"><PasswordForm required={false} onUpdated={onUpdated} /></div>}
            {section === "system" && (
              <div className="system-card page-card">
                <div><h3>API 与数据库</h3><span role="status" className={`connection ${connection}`}><span className="connection-dot" />{connectionLabels[connection]}</span></div>
                <button type="button" className="button" onClick={recheck} disabled={connection === "checking"}>
                  <ReloadOutlined aria-hidden="true" /> 重新检查
                </button>
              </div>
            )}
          </main>
        )}
      </div>
    </div>
  );
}
