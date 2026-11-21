import { useTranslation } from "react-i18next";
import { SidebarProjects } from "./SidebarProjects";
import { WorkbenchViewMenu } from "./WorkbenchViewMenu";

export function Sidebar({
  pendingReviews,
  open,
  workspaceState,
  onToggle,
  onRefresh,
  refreshing,
}: {
  pendingReviews: number | undefined;
  open: boolean;
  workspaceState: string;
  onToggle: () => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const { t } = useTranslation();
  return (
    <nav className="sidebar project-sidebar" id="sidebar" aria-label={t("projects.title")}>
      <div className="brand">
        <WorkbenchViewMenu current="workbench" pendingReviews={pendingReviews} />
        <button
          type="button"
          className="icon-button sidebar-refresh"
          aria-label={t("sidebar.refresh")}
          title={t("sidebar.refresh")}
          disabled={refreshing}
          onClick={onRefresh}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 11a8 8 0 1 0-2.3 5.7" />
            <path d="M20 4v7h-7" />
          </svg>
        </button>
        <button
          type="button"
          className="icon-button"
          aria-expanded={open}
          aria-controls="sidebar"
          aria-label={open ? t("sidebar.collapse") : t("sidebar.expand")}
          onClick={onToggle}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
            <path d="M9.5 4.5v15" />
          </svg>
        </button>
      </div>
      <SidebarProjects open={open} onExpand={onToggle} />
      <footer className="sidebar-foot">
        <span title={workspaceState}>{workspaceState}</span>
      </footer>
    </nav>
  );
}
