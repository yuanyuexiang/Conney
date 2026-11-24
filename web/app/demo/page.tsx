"use client";

import { ArrowLeftOutlined, DatabaseOutlined, ExperimentOutlined, InboxOutlined } from "@ant-design/icons";
import Link from "next/link";
import { useState } from "react";
import { IntakeWorkspace } from "../intake-workspace";
import { describeSync } from "../workbench-status";
import { demoCounts, demoIntake } from "./demo-intake";

const views = [
  { id: "queue", label: "待处理", icon: InboxOutlined },
  { id: "archive", label: "收件档案", icon: DatabaseOutlined },
] as const;
const demoSync = describeSync(null, false);

export default function DemoPage() {
  const [view, setView] = useState<"queue" | "archive">("archive");
  const [revision, setRevision] = useState(0);
  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="demo-title">
          <span className="demo-title-icon"><ExperimentOutlined aria-hidden="true" /></span>
          <div><strong>Relay 演示工作台</strong><p>示例邮件和 Excel 样本</p></div>
        </div>
        <Link className="button demo-return" href="/">
          <ArrowLeftOutlined aria-hidden="true" /> 返回业务工作台
        </Link>
      </header>
      <div className="body-layout">
        <nav className="sidebar" aria-label="演示导航">
          <div className="nav-group">
            {views.map(({ id, label, icon: Icon }) => (
              <button key={id} type="button" className={`nav-item ${id === view ? "active" : ""}`}
                aria-current={id === view ? "page" : undefined} onClick={() => setView(id)}>
                <Icon aria-hidden="true" />{label}
              </button>
            ))}
          </div>
        </nav>
        <IntakeWorkspace key={view} view={view} counts={demoCounts} mailbox={null}
          sync={demoSync} revision={revision} refresh={() => setRevision((value) => value + 1)}
          onViewArchive={() => setView("archive")} source={demoIntake} />
      </div>
    </div>
  );
}
