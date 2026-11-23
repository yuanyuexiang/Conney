"use client";

import { DownOutlined, KeyOutlined, LogoutOutlined, UserOutlined } from "@ant-design/icons";
import { Dropdown, Modal, type MenuProps } from "antd";
import { useId, useRef, useState } from "react";
import { PasswordForm } from "./account-forms";
import { roleLabels, type User } from "./relay-api";

export function AccountMenu({ user, leaving, onLogout, onUpdated }: {
  user: User;
  leaving: boolean;
  onLogout: () => void;
  onUpdated: (user: User) => void;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();

  const menu: MenuProps = {
    id: menuId,
    "aria-label": "账号操作",
    selectable: false,
    items: [
      {
        type: "group",
        label: <div className="account-menu-info"><strong>{user.username}</strong><span>{roleLabels[user.role]}</span></div>,
        children: [
          { key: "password", label: "修改密码", icon: <KeyOutlined aria-hidden="true" />, disabled: leaving },
          { key: "logout", label: leaving ? "退出中…" : "退出登录", icon: <LogoutOutlined aria-hidden="true" />, disabled: leaving },
        ],
      },
    ],
    onClick: ({ key, domEvent }) => {
      domEvent.preventDefault();
      setMenuOpen(false);
      if (key === "password") setPasswordOpen(true);
      if (key === "logout") {
        trigger.current?.focus();
        onLogout();
      }
    },
  };

  function closePassword() {
    if (!saving) setPasswordOpen(false);
  }

  return (
    <>
      <Dropdown menu={menu} trigger={["click"]} placement="bottomRight" open={menuOpen}
        onOpenChange={setMenuOpen} classNames={{ menu: "account-popover-menu" }} autoFocus destroyOnHidden>
        <button type="button" className="account-trigger" ref={trigger} aria-label={`账号菜单，${user.username}`}
          aria-haspopup="menu" aria-expanded={menuOpen} aria-controls={menuOpen ? menuId : undefined}>
          <span className="account-trigger-icon"><UserOutlined aria-hidden="true" /></span>
          <span className="account-trigger-name" title={user.username}>{user.username}</span>
          <DownOutlined className="account-trigger-chevron" aria-hidden="true" />
        </button>
      </Dropdown>
      <Modal title="修改密码" open={passwordOpen} width={440} centered footer={null}
        destroyOnHidden keyboard={!saving} closable={saving ? false : { "aria-label": "关闭修改密码" }}
        mask={{ closable: false }}
        classNames={{ body: "account-dialog-body" }}
        focusable={{ trap: true, focusTriggerAfterClose: false }}
        onCancel={closePassword} afterClose={() => trigger.current?.focus()}>
        <PasswordForm required={false} onUpdated={onUpdated} onClose={closePassword} onSavingChange={setSaving} />
      </Modal>
    </>
  );
}
