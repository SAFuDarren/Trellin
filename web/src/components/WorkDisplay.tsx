import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Menu } from "./FloatingControls";
import { Chevron, WorkbenchIcon } from "./Icons";
import "../styles/work-display.css";

const layouts = ["board", "list", "table"] as const;
export type WorkLayout = (typeof layouts)[number];

const layoutIcons: Record<WorkLayout, string> = { board: "claims", list: "list", table: "table" };

export function useWorkLayout(section: "claims" | "agent-work", defaultLayout: WorkLayout) {
  const key = `trellin.work-display.${section}`;
  const [layout, setLayout] = useState(() => {
    try {
      const saved = window.localStorage.getItem(key);
      return layouts.find((layout) => layout === saved) ?? defaultLayout;
    } catch {
      return defaultLayout;
    }
  });
  function change(layout: WorkLayout) {
    setLayout(layout);
    try {
      window.localStorage.setItem(key, layout);
    } catch {
      // The menu still works when preference storage is unavailable.
    }
  }
  return [layout, change] as const;
}

export function WorkDisplayMenu({ layout, onChange }: { layout: WorkLayout; onChange: (layout: WorkLayout) => void }) {
  const { t } = useTranslation();
  return (
    <Menu
      label={t("workDisplay.display")}
      triggerClassName="button work-display-trigger"
      trigger={
        <>
          <WorkbenchIcon name={layoutIcons[layout]} />
          {t("workDisplay.display")}
          <Chevron />
        </>
      }
      placement="bottom-end"
      groups={[
        {
          options: layouts.map((value) => ({
            id: value,
            label: t(`workDisplay.${value}`),
            icon: <WorkbenchIcon name={layoutIcons[value]} />,
            current: value === layout,
            onSelect: () => onChange(value),
          })),
        },
      ]}
    />
  );
}

export function WorkStatusColumn({
  label,
  count,
  tone,
  children,
}: {
  label: string;
  count: number;
  tone: "ok" | "bad" | "warn" | "live" | "none";
  children: ReactNode;
}) {
  return (
    <section className="board-column" aria-label={label}>
      <header>
        <span className="status-dot" data-tone={tone} aria-hidden="true" />
        <span>{label}</span>
        <span>{count}</span>
      </header>
      {children}
    </section>
  );
}

export function WorkTable({ label, columns, children }: { label: string; columns: string[]; children: ReactNode }) {
  return (
    <div className="page-body work-display-grid">
      <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
        <table className="table" aria-label={label}>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>{children}</tbody>
        </table>
      </div>
    </div>
  );
}
