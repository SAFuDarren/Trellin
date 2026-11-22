import { skipToken, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  desktopProjectLibrary,
  desktopProjectName,
  type DesktopProjectAction,
  type DesktopProjectLibrary,
} from "../workbench/desktop";
import { Popover } from "./FloatingControls";
import "../styles/sidebar-projects.css";

type SidebarProjectsProps = { open: boolean; onExpand?: () => void };

export function useDesktopProjectList(library = desktopProjectLibrary()) {
  return useQuery({
    queryKey: ["desktop-projects"],
    queryFn: library?.list ?? skipToken,
    retry: false,
  });
}

export function SidebarProjects(props: SidebarProjectsProps) {
  const { t } = useTranslation();
  const library = desktopProjectLibrary();
  if (library) return <RegisteredProjects {...props} library={library} />;
  return (
    props.open && (
      <section className="sidebar-projects" aria-labelledby="sidebar-projects-title">
        <header>
          <h2 id="sidebar-projects-title">{t("projects.title")}</h2>
        </header>
        <span className="sidebar-projects-row" aria-current="page">
          {t("projects.current")}
        </span>
      </section>
    )
  );
}

/** The current project at the top of the rail, like Multica's workspace tile. It opens the project switcher. */
export function ProjectTile({ fallback }: { fallback: ReactNode }) {
  const { t } = useTranslation();
  const id = useId();
  const library = desktopProjectLibrary();
  const list = useDesktopProjectList(library);
  if (!library) return fallback;
  const active = list.data?.projects.find((project) => project.id.toLowerCase() === list.data?.activeId);
  const name = active?.name ?? desktopProjectName() ?? t("projects.title");
  const initial = Array.from(name.trim())[0]?.toLocaleUpperCase() ?? "T";
  return (
    <Popover
      label={t("projects.switch", { name })}
      panelId={id}
      popup="dialog"
      triggerClassName="rail-project-tile"
      triggerTitle={t("projects.switch", { name })}
      className="project-switcher-panel"
      placement="bottom-start"
      trigger={<span aria-hidden="true">{initial}</span>}
    >
      {() => <RegisteredProjects open library={library} />}
    </Popover>
  );
}

function Folder() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z" />
    </svg>
  );
}

function RegisteredProjects({ library, open, onExpand }: SidebarProjectsProps & { library: DesktopProjectLibrary }) {
  const { t } = useTranslation();
  const titleId = useId();
  const [opening, setOpening] = useState(false);
  const [actionError, setActionError] = useState(false);
  const busy = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);
  const focusHeading = useRef(false);
  const list = useDesktopProjectList(library);
  const activeId = list.data?.activeId;

  useEffect(() => {
    if (!open || !focusHeading.current) return;
    focusHeading.current = false;
    heading.current?.focus();
  }, [open]);

  async function act(request: DesktopProjectAction) {
    if (busy.current) return;
    busy.current = true;
    setOpening(true);
    setActionError(false);
    try {
      await library.act(request);
    } catch {
      setActionError(true);
      void list.refetch();
    } finally {
      busy.current = false;
      setOpening(false);
    }
  }

  if (!open) {
    return (
      <button
        type="button"
        className="menu-item"
        aria-label={t("projects.title")}
        title={t("projects.title")}
        onClick={() => {
          focusHeading.current = true;
          onExpand?.();
        }}
      >
        <Folder />
      </button>
    );
  }

  return (
    <section className="sidebar-projects" aria-labelledby={titleId}>
      <header>
        <h2 id={titleId} ref={heading} tabIndex={-1}>
          {t("projects.title")}
        </h2>
        <button
          type="button"
          className="icon-button"
          aria-label={t("projects.new")}
          title={t("projects.new")}
          aria-disabled={opening || undefined}
          onClick={() => void act({ action: "new" })}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M12 5v14M5 12h14" />
          </svg>
        </button>
      </header>
      {list.isPending && <p role="status">{t("common.loading")}</p>}
      {list.isError && (
        <div className="sidebar-projects-status">
          <p role="alert">{t("projects.listFailed")}</p>
          <button className="button" type="button" disabled={list.isFetching} onClick={() => void list.refetch()}>
            {t("projects.retry")}
          </button>
        </div>
      )}
      {list.isSuccess && list.data.projects.length === 0 && <p>{t("projects.empty")}</p>}
      {list.data && list.data.projects.length > 0 && (
        <ul className="sidebar-projects-list">
          {list.data.projects.map((project) => {
            const current = project.id.toLowerCase() === activeId;
            return (
              <li key={project.id}>
                <button
                  type="button"
                  className="sidebar-projects-row"
                  aria-current={current ? "page" : undefined}
                  aria-disabled={opening || undefined}
                  disabled={!project.available || current}
                  title={project.name}
                  onClick={() => void act({ action: "open", id: project.id })}
                >
                  <span className="sidebar-projects-name">{project.name}</span>
                  {!project.available && (
                    <span className="sidebar-projects-unavailable">{t("projects.unavailable")}</span>
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {opening && <p role="status">{t("projects.opening")}</p>}
      {actionError && <p role="alert">{t("projects.actionFailed")}</p>}
      {list.isSuccess && list.data.projects.some((project) => !project.available) && (
        <button
          className="sidebar-projects-reload conversation-text-action"
          type="button"
          disabled={opening || list.isFetching}
          onClick={() => void list.refetch()}
        >
          {t("projects.retry")}
        </button>
      )}
    </section>
  );
}
