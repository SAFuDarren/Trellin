import { useTranslation } from "react-i18next";
import { useSpaceGroup } from "./SectionLayout";

/** The Home space's panel (#289): its entries, with projects switched from the rail tile. */
export function Sidebar({
  open,
  workspaceState,
  onToggle,
}: {
  open: boolean;
  workspaceState: string;
  onToggle: () => void;
}) {
  const { t } = useTranslation();
  const group = useSpaceGroup("home");
  return (
    <nav className="sidebar project-sidebar" id="sidebar" aria-labelledby="home-space-title">
      <div className="brand">
        {open && (
          <h1 id="home-space-title" className="sidebar-space-title">
            {t("spaces.home")}
          </h1>
        )}
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
      {open && group && (
        <div className="nav-group sidebar-space-entries">
          {group.entries.map((entry) => (
            <a
              key={entry.key}
              className="nav-item"
              href={entry.href}
              aria-current={entry.current ? "page" : undefined}
              title={entry.label}
            >
              <span className="nav-line">
                <span className="label">{entry.label}</span>
              </span>
            </a>
          ))}
        </div>
      )}
      <footer className="sidebar-foot">
        <span title={workspaceState}>{workspaceState}</span>
      </footer>
    </nav>
  );
}
