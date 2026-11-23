"use client";

import {
  CloudServerOutlined,
  DatabaseOutlined,
  InboxOutlined,
  KeyOutlined,
  LogoutOutlined,
  MailOutlined,
  ReloadOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import Image from "next/image";
import { useEffect, useState } from "react";
import { AccountLayout, PasswordForm, SignIn } from "./account-forms";
import { IntakeWorkspace } from "./intake-workspace";
import { MailboxSettings } from "./mailbox-settings";
import { describeSync, formatSyncTime, type IntakeCounts, type MailboxStatus } from "./workbench-status";
import { messageOf, relayRequest, RelayRequestError, roleLabels, type User } from "./relay-api";
import { UserManagement } from "./user-management";

const navigation = [
  { label: "工作台", items: [
    { id: "queue", label: "待处理", icon: InboxOutlined, adminOnly: false },
    { id: "archive", label: "收件档案", icon: DatabaseOutlined, adminOnly: false },
  ] },
  { label: "管理", items: [
    { id: "mailbox", label: "邮箱配置", icon: MailOutlined, adminOnly: true },
    { id: "users", label: "用户管理", icon: TeamOutlined, adminOnly: true },
    { id: "password", label: "修改密码", icon: KeyOutlined, adminOnly: false },
    { id: "system", label: "系统状态", icon: CloudServerOutlined, adminOnly: false },
  ] },
] as const;
type Section = (typeof navigation)[number]["items"][number]["id"];

function Brand() {
  return (
    <div className="brand">
      <Image src="/brand/knn-logo.png" width={152} height={38} alt="康尼新能源" unoptimized priority />
      <span className="brand-divider" />
      <span className="product-name">EDI 交付工作台</span>
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

  const exit = <button type="button" className="button" disabled={leaving} onClick={() => void logout()}><LogoutOutlined aria-hidden="true" /> 退出登录</button>;
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
  const [section, setSection] = useState<Section>("queue");
  const [mailbox, setMailbox] = useState<MailboxStatus | null>();
  const [statusError, setStatusError] = useState(false);
  const [counts, setCounts] = useState<IntakeCounts>();
  const [revision, setRevision] = useState(0);
  const sync = describeSync(mailbox, statusError);
  const parsing = Boolean(counts?.queued);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function readStatus() {
      if (pending) return;
      pending = true;
      try {
        const values = await relayRequest<MailboxStatus | null>("/mailbox/status", { signal: controller.signal });
        if (!controller.signal.aborted) {
          setMailbox(values);
          setStatusError(false);
        }
      } catch {
        if (!controller.signal.aborted) setStatusError(true);
      } finally {
        pending = false;
      }
    }
    void readStatus();
    const interval = window.setInterval(() => {
      if (!document.hidden) void readStatus();
    }, 15000);
    return () => {
      controller.abort();
      window.clearInterval(interval);
    };
  }, [revision]);

  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    async function readCounts() {
      if (pending) return;
      pending = true;
      try {
        const values = await relayRequest<IntakeCounts>("/intake/counts", { signal: controller.signal });
        if (!controller.signal.aborted) setCounts(values);
      } catch {
        if (!controller.signal.aborted) setCounts(undefined);
      } finally {
        pending = false;
      }
    }
    void readCounts();
    const interval = parsing ? window.setInterval(() => {
      if (!document.hidden) void readCounts();
    }, 12000) : undefined;
    return () => {
      controller.abort();
      if (interval !== undefined) window.clearInterval(interval);
    };
  }, [section, revision, mailbox?.last_sync_at, parsing]);

  function refresh() {
    setRevision((value) => value + 1);
  }

  const admin = user.role === "admin";
  const allowed = navigation.flatMap((group) => [...group.items]).filter((item) => admin || !item.adminOnly);
  const active = allowed.find((item) => item.id === section) ?? allowed[0];
  const actionable = counts ? counts.failed + counts.review + counts.queued : 0;

  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-meta">
          <span className={`connection sync-indicator ${sync.tone}`} role="status">
            <span className="connection-dot" />
            {sync.label}
            {mailbox?.last_sync_at && <time dateTime={mailbox.last_sync_at} title="最近一次同步时间">{formatSyncTime(mailbox.last_sync_at)}</time>}
          </span>
          <span className="account-identity">{user.username} <span>· {roleLabels[user.role]}</span></span>
          {exit}
        </div>
      </header>
      {logoutError && <p className="notice failed" role="alert">{logoutError}</p>}
      <div className="body-layout">
        <nav className="sidebar" aria-label="主导航">
          {navigation.map((group) => (
            <div className="nav-group" key={group.label}>
              <span className="sidebar-label">{group.label}</span>
              {group.items.filter((item) => admin || !item.adminOnly).map(({ id, label, icon: Icon }) => (
                <button className={`nav-item ${active.id === id ? "active" : ""}`} type="button"
                  aria-current={active.id === id ? "page" : undefined} key={id} onClick={() => setSection(id)}>
                  <Icon aria-hidden="true" />{label}
                  {id === "queue" && actionable > 0 && <span className="nav-count">{actionable}</span>}
                </button>
              ))}
            </div>
          ))}
          <div className="sidebar-footer"><strong>Relay</strong><span>康尼新能源</span></div>
        </nav>

        {active.id === "queue" || active.id === "archive" ? (
          <IntakeWorkspace key={active.id} view={active.id} counts={counts} sync={sync}
            mailbox={mailbox} revision={revision} refresh={refresh} onViewArchive={() => setSection("archive")}
            onConfigure={admin ? () => setSection("mailbox") : undefined} />
        ) : (
          <main className="full-page">
            <h1>{active.label}</h1>
            {active.id === "mailbox" && <MailboxSettings onChanged={refresh} />}
            {active.id === "users" && <UserManagement current={user} />}
            {active.id === "password" && <div className="page-card mailbox-card"><PasswordForm required={false} onUpdated={onUpdated} /></div>}
            {active.id === "system" && <SystemStatus />}
          </main>
        )}
      </div>
    </div>
  );
}

const connectionLabels = {
  checking: "检查中",
  ready: "服务就绪",
  unavailable: "服务暂不可用",
};

function SystemStatus() {
  const [connection, setConnection] = useState<keyof typeof connectionLabels>("checking");
  const [check, setCheck] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/v1/health/ready", { cache: "no-store", signal: controller.signal })
      .then((response) => setConnection(response.ok ? "ready" : "unavailable"))
      .catch(() => { if (!controller.signal.aborted) setConnection("unavailable"); });
    return () => controller.abort();
  }, [check]);

  return (
    <div className="system-card page-card">
      <div><h3>API 与数据库</h3><span role="status" className={`connection ${connection}`}><span className="connection-dot" />{connectionLabels[connection]}</span></div>
      <button type="button" className="button" disabled={connection === "checking"}
        onClick={() => { setConnection("checking"); setCheck((previous) => previous + 1); }}>
        <ReloadOutlined aria-hidden="true" /> 重新检查
      </button>
    </div>
  );
}
