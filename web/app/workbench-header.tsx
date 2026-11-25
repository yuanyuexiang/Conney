import type { ReactNode } from "react";

export type WorkbenchHeading = { title: string; icon: ReactNode };

export function WorkbenchHeader({ title, icon, meta, actions }: WorkbenchHeading & {
  meta?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className="workbench-heading">
      <div className="workbench-heading-main">
        <span className="workbench-heading-icon" aria-hidden="true">{icon}</span>
        <div className="workbench-heading-text">
          <h1>{title}</h1>
          {meta && <div className="workbench-heading-meta">{meta}</div>}
        </div>
      </div>
      {actions && <div className="workbench-heading-actions">{actions}</div>}
    </header>
  );
}
