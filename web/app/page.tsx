"use client";

import {
  CloudServerOutlined,
  DatabaseOutlined,
  DashboardOutlined,
  FileExcelOutlined,
  LogoutOutlined,
  MailOutlined,
  ProfileOutlined,
  TeamOutlined,
  TagsOutlined,
} from "@ant-design/icons";
import Image from "next/image";
import { useEffect, useState } from "react";
import { AccountLayout, PasswordForm, SignIn } from "./account-forms";
import { AccountMenu } from "./account-menu";
import { IntakeWorkspace, type MailFilter } from "./intake-workspace";
import { Dashboard } from "./dashboard";
import { DeliveryWorkspace } from "./delivery-workspace";
import { MaterialMappingPage } from "./material-mapping";
import { MailboxSettings } from "./mailbox-settings";
import { describeSync, formatSyncTime, type IntakeCounts, type MailboxStatus } from "./workbench-status";
import { messageOf, relayRequest, RelayRequestError, type User } from "./relay-api";
import { UserManagement } from "./user-management";
import { SystemStatus } from "./system-status";
import { WorkbenchHeader } from "./workbench-header";
import { TemplateRulesPage } from "./template-rules";
import type { TemplateAttachment } from "./template-rules-api";

const navigation = [
  { label: "工作台", items: [
    { id: "dashboard", label: "仪表盘", icon: DashboardOutlined, adminOnly: false },
    { id: "archive", label: "收件档案", icon: DatabaseOutlined, adminOnly: false },
    { id: "delivery", label: "交付计划", icon: FileExcelOutlined, adminOnly: false },
  ] },
  { label: "管理", items: [
    { id: "mappings", label: "物料匹配", icon: TagsOutlined, adminOnly: true },
    { id: "templates", label: "模板规则", icon: ProfileOutlined, adminOnly: false },
    { id: "mailbox", label: "邮箱配置", icon: MailOutlined, adminOnly: true },
    { id: "users", label: "用户管理", icon: TeamOutlined, adminOnly: true },
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

  return <Workspace user={user} onUpdated={setUser} onLogout={() => void logout()}
    leaving={leaving} logoutError={logoutError} />;
}

function Workspace({ user, onUpdated, onLogout, leaving, logoutError }: {
  user: User;
  onUpdated: (user: User) => void;
  onLogout: () => void;
  leaving: boolean;
  logoutError: string;
}) {
  const [section, setSection] = useState<Section>("dashboard");
  const [archiveFilter, setArchiveFilter] = useState<MailFilter>("all");
  const [draftId, setDraftId] = useState<number>();
  const [archiveMailId, setArchiveMailId] = useState<number>();
  const [templateSource, setTemplateSource] = useState<TemplateAttachment>();
  const [deliveryReview, setDeliveryReview] = useState(false);
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

  function navigate(id: Section) {
    setArchiveMailId(undefined);
    setTemplateSource(undefined);
    setDeliveryReview(false);
    setArchiveFilter("all");
    setSection(id);
  }

  function openArchive(mailId?: number) {
    navigate("archive");
    setArchiveMailId(mailId);
  }

  function openArchiveFilter(filter: MailFilter) {
    navigate("archive");
    setArchiveFilter(filter);
  }

  function openTemplate(source: TemplateAttachment) {
    navigate("templates");
    setTemplateSource(source);
  }

  function openDeliveryReview() {
    navigate("delivery");
    setDraftId(undefined);
    setDeliveryReview(true);
  }

  const admin = user.role === "admin";
  const allowed = navigation.flatMap((group) => [...group.items]).filter((item) => admin || !item.adminOnly);
  const active = allowed.find((item) => item.id === section) ?? allowed[0];
  const HeadingIcon = active.icon;
  const heading = { title: active.label, icon: <HeadingIcon /> };

  return (
    <div className="app-shell">
      <header className="topbar">
        <Brand />
        <div className="topbar-meta">
          <span className={`connection sync-indicator ${sync.tone}`} role="status">
            <span className="connection-dot" />
            <span className="sync-indicator-label">{sync.label}</span>
            {mailbox?.last_sync_at && <time dateTime={mailbox.last_sync_at} title="最近一次同步时间">{formatSyncTime(mailbox.last_sync_at)}</time>}
          </span>
          <AccountMenu user={user} leaving={leaving} onLogout={onLogout} onUpdated={onUpdated} />
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
                  aria-current={active.id === id ? "page" : undefined} key={id} onClick={() => navigate(id)}>
                  <Icon aria-hidden="true" />{label}
                </button>
              ))}
            </div>
          ))}
          <div className="sidebar-footer"><strong>Relay</strong><span>康尼新能源</span></div>
        </nav>

        {active.id === "dashboard" ? (
          <Dashboard heading={heading} counts={counts} sync={sync} syncAt={mailbox?.last_sync_at} revision={revision}
            onRefresh={refresh} onArchive={() => openArchive()} onMail={openArchive} onArchiveFilter={openArchiveFilter}
            onDelivery={() => { setDraftId(undefined); navigate("delivery"); }}
            onReview={openDeliveryReview} onConfigure={admin ? () => navigate("mailbox") : undefined} />
        ) : active.id === "delivery" ? (
          <DeliveryWorkspace key={String(deliveryReview)} heading={heading}
            admin={admin} draftId={draftId} onSelect={setDraftId}
            initialReview={deliveryReview} onArchive={openArchive} />
        ) : active.id === "archive" ? (
          <IntakeWorkspace key={`${archiveFilter}:${archiveMailId ?? ""}`} heading={heading} counts={counts} sync={sync}
            mailbox={mailbox} revision={revision} refresh={refresh}
            initialMailId={archiveMailId} initialFilter={archiveFilter}
            onTemplate={openTemplate}
            onDraftOpened={admin ? (id) => { setDraftId(id); navigate("delivery"); } : undefined}
            onConfigure={admin ? () => navigate("mailbox") : undefined} />
        ) : active.id === "system" ? (
          <SystemStatus heading={heading} onConfigure={admin ? () => navigate("mailbox") : undefined}
            onViewParsingMail={() => openArchiveFilter("queued")} />
        ) : (
          <main className="workbench management-page">
            <WorkbenchHeader {...heading} />
            {active.id === "mailbox" && <MailboxSettings onChanged={refresh} />}
            {active.id === "users" && <UserManagement current={user} />}
            {active.id === "mappings" && <MaterialMappingPage />}
            {active.id === "templates" && <TemplateRulesPage
              key={templateSource ? `${templateSource.code}:${templateSource.attachmentId ?? ""}` : "catalog"}
              admin={admin} source={templateSource} onArchive={openArchive} />}
          </main>
        )}
      </div>
    </div>
  );
}
