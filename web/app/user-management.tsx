"use client";

import { UserAddOutlined } from "@ant-design/icons";
import { Modal } from "antd";
import { type FormEvent, useEffect, useId, useRef, useState } from "react";
import { messageOf, relayRequest, roleLabels, type Role, type User } from "./relay-api";
import { WorkbenchSelect } from "./workbench-select";

const roleOptions = (["viewer", "admin"] as const).map((value) => ({ value, label: roleLabels[value] }));

function RoleField({ initial, disabled }: { initial: Role; disabled: boolean }) {
  const [role, setRole] = useState(initial);
  return <>
    <label>角色
      <WorkbenchSelect label="角色" options={roleOptions} value={role}
        onChange={setRole} disabled={disabled} />
    </label>
    <input type="hidden" name="role" value={role} />
  </>;
}

export function UserManagement({ current }: { current: User }) {
  const [users, setUsers] = useState<User[]>();
  const [selectedId, setSelectedId] = useState<number>();
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ error: boolean; text: string }>();
  const [adding, setAdding] = useState(false);
  const [createError, setCreateError] = useState("");
  const [removing, setRemoving] = useState<User>();
  const [removeError, setRemoveError] = useState("");
  const addButton = useRef<HTMLButtonElement>(null);
  const usernameInput = useRef<HTMLInputElement>(null);
  const deleteTrigger = useRef<HTMLButtonElement>(null);
  const usernameHint = useId();
  const passwordHint = useId();
  const selected = users?.find((user) => user.id === selectedId);

  useEffect(() => {
    const controller = new AbortController();
    relayRequest<User[]>("/users", { signal: controller.signal })
      .then(setUsers)
      .catch((error) => { if (!controller.signal.aborted) setStatus({ error: true, text: messageOf(error) }); });
    return () => controller.abort();
  }, []);

  function closeAddUser() {
    if (!busy) setAdding(false);
  }

  async function createUser(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !users) return;
    const fields = new FormData(event.currentTarget);
    setBusy(true);
    setCreateError("");
    try {
      const account = await relayRequest<User>("/users", {
        method: "POST",
        body: JSON.stringify({
          username: String(fields.get("username")).trim().toLowerCase(),
          role: fields.get("role"),
          password: fields.get("password"),
        }),
      });
      setUsers((previous) => [...(previous ?? []), account]);
      setStatus({ error: false, text: `已添加账号「${account.username}」。` });
      setAdding(false);
    } catch (reason) {
      setCreateError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }

  async function save(event: FormEvent<HTMLFormElement>, operation: "access" | "password") {
    event.preventDefault();
    if (busy || !selected) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    setBusy(true);
    setStatus(undefined);
    try {
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
    } catch (reason) {
      setStatus({ error: true, text: messageOf(reason) });
    } finally {
      setBusy(false);
    }
  }

  async function removeUser() {
    if (busy || !removing) return;
    setBusy(true);
    setRemoveError("");
    try {
      await relayRequest(`/users/${removing.id}`, { method: "DELETE" });
      setUsers((previous) => previous?.filter((user) => user.id !== removing.id));
      setSelectedId((previous) => previous === removing.id ? undefined : previous);
      setStatus({ error: false, text: `已删除账号「${removing.username}」。` });
      setRemoving(undefined);
    } catch (reason) {
      setRemoveError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="user-management">
      {status && <p role={status.error ? "alert" : "status"} className={`notice ${status.error ? "failed" : ""}`}>{status.text}</p>}
      <section className="page-card">
        <div className="section-heading user-list-heading">
          <h2>账号列表</h2>
          {users && <span>{users.length} 个账号</span>}
          <button ref={addButton} type="button" className="button primary-button" disabled={busy || !users}
            aria-haspopup="dialog" onClick={() => {
              setCreateError("");
              setStatus(undefined);
              setAdding(true);
            }}>
            <UserAddOutlined aria-hidden="true" /> 添加用户
          </button>
        </div>
        <div className="table-scroll">
          <table><thead><tr><th scope="col">用户名</th><th scope="col">角色</th><th scope="col">状态</th><th scope="col">操作</th></tr></thead>
            <tbody>{users?.map((user) => (
              <tr key={user.id}>
                <td>{user.username}{user.id === current.id && "（当前账号）"}</td>
                <td>{roleLabels[user.role]}</td>
                <td>{user.active ? user.must_change_password ? "待修改密码" : "已启用" : "已停用"}</td>
                <td>{user.id !== current.id && <div className="user-row-actions">
                  <button type="button" className="text-button" onClick={() => {
                    setSelectedId(user.id);
                    setStatus(undefined);
                  }} disabled={busy}>管理</button>
                  <button type="button" className="text-button delete-user-button"
                    aria-label={`删除用户 ${user.username}`} aria-haspopup="dialog" onClick={(event) => {
                      deleteTrigger.current = event.currentTarget;
                      setRemoveError("");
                      setStatus(undefined);
                      setRemoving(user);
                    }} disabled={busy}>删除</button>
                </div>}</td>
              </tr>
            ))}</tbody></table>
        </div>
      </section>

      {selected && (
        <section className="page-card mailbox-card" key={selected.id}>
          <div className="section-heading"><h2>管理 {selected.username}</h2><button type="button" className="button" disabled={busy} onClick={() => setSelectedId(undefined)}>关闭</button></div>
          <form className="account-form" onSubmit={(event) => void save(event, "access")}>
            <RoleField initial={selected.role} disabled={busy} />
            <label className="checkbox-row"><input name="active" type="checkbox" defaultChecked={selected.active} /> 启用账号</label>
            <button type="submit" className="button primary-button" disabled={busy}>保存权限</button>
          </form>
          <form className="account-form user-password-form" onSubmit={(event) => void save(event, "password")}>
            <label>新临时密码<input name="password" type="password" minLength={8} maxLength={128} autoComplete="new-password" required /></label>
            <button type="submit" className="button" disabled={busy}>重置密码</button>
          </form>
        </section>
      )}

      <Modal title="添加用户" open={adding} width={440} centered footer={null}
        destroyOnHidden keyboard={!busy} closable={busy ? false : { "aria-label": "关闭添加用户" }}
        mask={{ closable: false }} classNames={{ body: "account-dialog-body" }}
        focusable={{ trap: true, focusTriggerAfterClose: false }} onCancel={closeAddUser}
        afterOpenChange={(open) => { if (open) usernameInput.current?.focus(); }}
        afterClose={() => addButton.current?.focus()}>
        <form className="account-form" onSubmit={(event) => void createUser(event)}>
          <label>用户名<input ref={usernameInput} name="username" required minLength={3} maxLength={64}
            autoComplete="off" aria-describedby={usernameHint} disabled={busy} /></label>
          <p id={usernameHint} className="field-hint">以英文字母开头，可用小写字母、数字、下划线、点或短横线。</p>
          <RoleField initial="viewer" disabled={busy} />
          <label>临时密码<input name="password" type="password" minLength={8} maxLength={128}
            autoComplete="new-password" aria-describedby={passwordHint} required disabled={busy} /></label>
          <p id={passwordHint} className="field-hint">至少 8 个字符，用户首次登录时需修改。</p>
          {createError && <p className="notice failed" role="alert">{createError}</p>}
          <div className="account-form-actions">
            <button type="button" className="button" onClick={closeAddUser} disabled={busy}>取消</button>
            <button type="submit" className="button primary-button" disabled={busy}>
              {busy ? "创建中…" : "创建账号"}
            </button>
          </div>
        </form>
      </Modal>
      <Modal title="删除用户" open={removing !== undefined} width={440} centered
        okText="确认删除" cancelText="取消" okButtonProps={{ danger: true }}
        cancelButtonProps={{ disabled: busy, autoFocus: true }}
        confirmLoading={busy} keyboard={!busy} closable={busy ? false : { "aria-label": "关闭删除确认" }}
        mask={{ closable: false }} onOk={() => void removeUser()}
        focusable={{ trap: true, focusTriggerAfterClose: false }}
        onCancel={() => { if (!busy) setRemoving(undefined); }}
        afterClose={() => {
          (deleteTrigger.current?.isConnected ? deleteTrigger.current : addButton.current)?.focus();
        }}>
        <p>删除账号「{removing?.username}」后，该用户将从列表移除，现有会话立即失效。历史交付记录会保留。</p>
        {removeError && <p className="notice failed" role="alert">{removeError}</p>}
      </Modal>
    </div>
  );
}
