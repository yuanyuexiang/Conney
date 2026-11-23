"use client";

import { type FormEvent, useEffect, useState } from "react";
import { messageOf, relayRequest, roleLabels, type Role, type User } from "./relay-api";

export function UserManagement({ current }: { current: User }) {
  const [users, setUsers] = useState<User[]>();
  const [selectedId, setSelectedId] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ error: boolean; text: string }>();
  const selected = users?.find((user) => user.id === selectedId);

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<User[]>("/users", { signal: controller.signal })
      .then(setUsers)
      .catch((error) => { if (!controller.signal.aborted) setStatus({ error: true, text: messageOf(error) }); });
    return () => controller.abort();
  }, []);

  async function save(event: FormEvent<HTMLFormElement>, operation: "create" | "access" | "password") {
    event.preventDefault();
    const form = event.currentTarget;
    const fields = new FormData(form);
    setBusy(true);
    setStatus(undefined);
    try {
      if (operation === "create") {
        const account = await relayRequest<User>("/users", {
          method: "POST",
          body: JSON.stringify({ username: String(fields.get("username")).trim().toLowerCase(), role: fields.get("role"), password: fields.get("password") }),
        });
        setUsers((previous) => [...(previous ?? []), account]);
        form.reset();
        setStatus({ error: false, text: "账号已创建。用户首次登录后需要更改临时密码。" });
      } else if (selected) {
        const path = `/users/${selected.id}`;
        if (operation === "access") {
          const account = await relayRequest<User>(path, {
            method: "PATCH",
            body: JSON.stringify({ role: fields.get("role") as Role, active: fields.get("active") === "on" }),
          });
          setUsers((previous) => previous?.map((user) => user.id === account.id ? account : user));
          setStatus({ error: false, text: "权限已更新，该用户需要重新登录。" });
        } else {
          await relayRequest(path + "/password", {
            method: "POST",
            body: JSON.stringify({ password: fields.get("password") }),
          });
          setUsers((previous) => previous?.map((user) => user.id === selected.id ? { ...user, must_change_password: true } : user));
          form.reset();
          setStatus({ error: false, text: "临时密码已重置，原登录会话已撤销。" });
        }
      }
    } catch (reason) {
      setStatus({ error: true, text: messageOf(reason) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="user-management">
      {status && <p role={status.error ? "alert" : "status"} className={`notice ${status.error ? "failed" : ""}`}>{status.text}</p>}
      <section className="page-card">
        <div className="section-heading"><h3>账号列表</h3><span>{users?.length ?? 0} 个账号</span></div>
        <div className="table-scroll">
          <table><thead><tr><th scope="col">用户名</th><th scope="col">角色</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
            <tbody>{users?.map((user) => (
              <tr key={user.id}>
                <td>{user.username}{user.id === current.id && "（当前账号）"}</td>
                <td>{roleLabels[user.role]}</td>
                <td>{user.active ? user.must_change_password ? "待修改密码" : "已启用" : "已停用"}</td>
                <td>{user.id !== current.id && <button type="button" className="text-button" onClick={() => { setSelectedId(user.id); setStatus(undefined); }} disabled={busy}>管理</button>}</td>
              </tr>
            ))}</tbody></table>
        </div>
      </section>

      <section className="page-card mailbox-card">
        <h2>创建用户</h2>
        <form className="account-form" onSubmit={(event) => void save(event, "create")}>
          <label>用户名<input name="username" required minLength={3} maxLength={64} autoComplete="off" /></label>
          <p className="field-hint">以英文字母开头，可用小写字母、数字、下划线、点或短横线。</p>
          <label>角色<select name="role" defaultValue="viewer"><option value="viewer">查询用户</option><option value="admin">管理员</option></select></label>
          <label>临时密码<input name="password" type="password" minLength={8} maxLength={128} autoComplete="new-password" required /></label>
          <button type="submit" className="button primary-button" disabled={busy || !users}>创建账号</button>
        </form>
      </section>

      {selected && (
        <section className="page-card mailbox-card" key={selected.id}>
          <div className="section-heading"><h2>管理 {selected.username}</h2><button type="button" className="button" disabled={busy} onClick={() => setSelectedId(undefined)}>关闭</button></div>
          <form className="account-form" onSubmit={(event) => void save(event, "access")}>
            <label>角色<select name="role" defaultValue={selected.role}><option value="viewer">查询用户</option><option value="admin">管理员</option></select></label>
            <label className="checkbox-row"><input name="active" type="checkbox" defaultChecked={selected.active} /> 启用账号</label>
            <button type="submit" className="button primary-button" disabled={busy}>保存权限</button>
          </form>
          <form className="account-form user-password-form" onSubmit={(event) => void save(event, "password")}>
            <label>新临时密码<input name="password" type="password" minLength={8} maxLength={128} autoComplete="new-password" required /></label>
            <button type="submit" className="button" disabled={busy}>重置密码</button>
          </form>
        </section>
      )}
    </div>
  );
}
