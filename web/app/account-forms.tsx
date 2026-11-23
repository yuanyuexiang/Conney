"use client";

import { EyeInvisibleOutlined, EyeOutlined } from "@ant-design/icons";
import Image from "next/image";
import type { FormEvent, ReactNode } from "react";
import { useState } from "react";
import { messageOf, relayRequest, type User } from "./relay-api";

export function AccountLayout({ children, action }: { children: ReactNode; action?: ReactNode }) {
  return (
    <main className="auth-layout">
      <div className="auth-cover" aria-hidden="true">
        <div className="auth-cover-copy">
          <p className="auth-wordmark">Relay</p>
          <p className="auth-cover-title">EDI 交付计划<br />管理平台</p>
          <p className="auth-cover-description">邮件采集、附件归档与交付计划核对</p>
        </div>
      </div>
      <section className="auth-panel" aria-label="账号入口">
        <header className="auth-brand">
          <Image src="/brand/knn-logo.png" width={178} height={44} alt="康尼新能源" unoptimized priority />
          {action}
        </header>
        <div className="auth-content">{children}</div>
        <footer className="auth-footer">南京康尼新能源汽车零部件有限公司</footer>
      </section>
    </main>
  );
}

export function SignIn({ onSignedIn }: { onSignedIn: (user: User) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [visiblePassword, setVisiblePassword] = useState(false);

  async function signIn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      onSignedIn(await relayRequest<User>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username: fields.get("username"), password: fields.get("password") }),
      }));
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <AccountLayout>
      <h1>登录工作台</h1>
      <p className="auth-instructions">请使用管理员分配的账号登录。</p>
      <form className="sign-in-form" onSubmit={(event) => void signIn(event)}>
        <label htmlFor="signin-username">用户名</label>
        <input id="signin-username" name="username" placeholder="请输入用户名"
          autoComplete="username" maxLength={64} required />
        <label htmlFor="signin-password">密码</label>
        <div className="auth-password-field">
          <input id="signin-password" name="password" placeholder="请输入密码"
            type={visiblePassword ? "text" : "password"} autoComplete="current-password" maxLength={128} required />
          <button type="button" className="auth-password-toggle"
            aria-label={visiblePassword ? "隐藏密码" : "显示密码"}
            onClick={() => setVisiblePassword((value) => !value)}>
            {visiblePassword ? <EyeInvisibleOutlined aria-hidden="true" /> : <EyeOutlined aria-hidden="true" />}
          </button>
        </div>
        {error && <p role="alert" className="notice failed">{error}</p>}
        <button className="button primary-button auth-submit" type="submit" disabled={busy}>{busy ? "登录中…" : "登录"}</button>
      </form>
    </AccountLayout>
  );
}

export function PasswordForm({ onUpdated, required }: { onUpdated: (user: User) => void; required: boolean }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    if (fields.get("replacement") !== fields.get("confirm")) {
      setError("两次输入的新密码不一致。");
      return;
    }
    setBusy(true);
    setError("");
    setDone(false);
    try {
      const user = await relayRequest<User>("/auth/password", {
        method: "POST",
        body: JSON.stringify({ current_password: fields.get("current"), new_password: fields.get("replacement") }),
      });
      form.reset();
      setDone(true);
      onUpdated(user);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="account-form" onSubmit={(event) => void submit(event)}>
      {required && <p className="field-hint">首次登录请先替换临时密码。</p>}
      <label>当前密码<input name="current" type="password" autoComplete="current-password" required maxLength={128} /></label>
      <label>新密码<input name="replacement" type="password" autoComplete="new-password" minLength={12} maxLength={128} required /></label>
      <label>确认新密码<input name="confirm" type="password" autoComplete="new-password" minLength={12} maxLength={128} required /></label>
      <p className="field-hint">密码至少 12 个字符。</p>
      {error && <p className="notice failed" role="alert">{error}</p>}
      {done && <p className="notice" role="status">密码已修改，其他登录会话已退出。</p>}
      <button className="button primary-button" type="submit" disabled={busy}>{busy ? "保存中…" : "保存新密码"}</button>
    </form>
  );
}
