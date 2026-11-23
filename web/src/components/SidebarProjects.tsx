import { skipToken, useQuery } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  DEFAULT_PROJECT_NAME,
  desktopProjectLibrary,
  desktopProjectName,
  type DesktopProject,
  type DesktopProjectAction,
  type DesktopProjectLibrary,
} from "../workbench/desktop";
import { Popover } from "./FloatingControls";
import "../styles/sidebar-projects.css";

type SidebarProjectsProps = { open: boolean; onExpand?: () => void };
type Managing = { id: string; mode: "actions" | "rename" | "remove" };

/** The project Trellin created keeps an English name on disk and is shown in the interface language. */
export function projectLabel(t: TFunction, project: DesktopProject) {
  return project.default && project.name === DEFAULT_PROJECT_NAME ? t("projects.defaultName") : project.name;
}

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
  const name = (active && projectLabel(t, active)) ?? desktopProjectName() ?? t("projects.title");
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
  const [actionError, setActionError] = useState<"open" | "manage" | null>(null);
  const [managing, setManaging] = useState<Managing | null>(null);
  const [draft, setDraft] = useState("");
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
    const manages = request.action === "rename" || request.action === "remove";
    setOpening(!manages);
    setActionError(null);
    try {
      await library.act(request);
      if (manages) {
        setManaging(null);
        void list.refetch();
      }
    } catch {
      setActionError(manages ? "manage" : "open");
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
            const label = projectLabel(t, project);
            const mode = managing?.id === project.id ? managing.mode : null;
            const panelId = `${titleId}-${project.id}`;
            return (
              <li key={project.id}>
                <div className="sidebar-projects-item">
                  <button
                    type="button"
                    className="sidebar-projects-row"
                    aria-current={current ? "page" : undefined}
                    aria-disabled={opening || undefined}
                    disabled={!project.available || current}
                    title={label}
                    onClick={() => void act({ action: "open", id: project.id })}
                  >
                    <span className="sidebar-projects-name">{label}</span>
                    {!project.available && (
                      <span className="sidebar-projects-unavailable">{t("projects.unavailable")}</span>
                    )}
                  </button>
                  <button
                    type="button"
                    className="icon-button sidebar-projects-more"
                    aria-label={t("projects.manage", { name: label })}
                    title={t("projects.manage", { name: label })}
                    aria-expanded={Boolean(mode)}
                    aria-controls={mode ? panelId : undefined}
                    onClick={() => {
                      setActionError(null);
                      setManaging(mode ? null : { id: project.id, mode: "actions" });
                    }}
                  >
                    <svg viewBox="0 0 24 24" aria-hidden="true">
                      <path d="M6 12h.01M12 12h.01M18 12h.01" />
                    </svg>
                  </button>
                </div>
                {mode && (
                  <ProjectManagement
                    id={panelId}
                    t={t}
                    mode={mode}
                    label={label}
                    current={current}
                    removable={!project.default}
                    draft={draft}
                    busy={busy.current}
                    onDraft={setDraft}
                    onMode={(next) => {
                      if (next === "rename") setDraft(label);
                      setManaging({ id: project.id, mode: next });
                    }}
                    onCancel={() => setManaging(null)}
                    onRename={() => void act({ action: "rename", id: project.id, name: draft.trim() })}
                    onRemove={() => void act({ action: "remove", id: project.id })}
                  />
                )}
              </li>
            );
          })}
        </ul>
      )}
      {opening && <p role="status">{t("projects.opening")}</p>}
      {actionError && (
        <p role="alert">{t(actionError === "manage" ? "projects.manageFailed" : "projects.actionFailed")}</p>
      )}
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

/** #302: rename a project or remove it from the list. Removing keeps its folder and database. */
function ProjectManagement({
  id,
  t,
  mode,
  label,
  current,
  removable,
  draft,
  busy,
  onDraft,
  onMode,
  onCancel,
  onRename,
  onRemove,
}: {
  id: string;
  t: TFunction;
  mode: Managing["mode"];
  label: string;
  current: boolean;
  removable: boolean;
  draft: string;
  busy: boolean;
  onDraft: (value: string) => void;
  onMode: (mode: "rename" | "remove") => void;
  onCancel: () => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  // The list scrolls, so keep the open panel and its buttons in view.
  useEffect(() => {
    const panel = document.getElementById(id);
    const list = panel?.closest<HTMLElement>(".sidebar-projects-list");
    if (!panel || !list) return;
    // Scroll only the list: scrollIntoView would also move the page behind the popover.
    const overflow = panel.getBoundingClientRect().bottom - list.getBoundingClientRect().bottom;
    if (overflow > 0) list.scrollTop += overflow;
  }, [id, mode]);
  if (mode === "rename") {
    const unchanged = !draft.trim() || draft.trim() === label;
    return (
      <form
        id={id}
        className="sidebar-projects-manage"
        onSubmit={(event) => {
          event.preventDefault();
          if (!unchanged) onRename();
        }}
      >
        <input
          aria-label={t("projects.renameLabel")}
          value={draft}
          maxLength={120}
          autoFocus
          onChange={(event) => onDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              event.stopPropagation();
              onCancel();
            }
          }}
        />
        <div className="sidebar-projects-manage-actions">
          <button type="submit" className="button primary" disabled={unchanged || busy}>
            {t("projects.save")}
          </button>
          <button type="button" className="button" onClick={onCancel}>
            {t("projects.cancel")}
          </button>
        </div>
      </form>
    );
  }
  if (mode === "remove") {
    return (
      <div id={id} className="sidebar-projects-manage" role="group" aria-label={t("projects.remove")}>
        <p>{t("projects.removeConfirm", { name: label })}</p>
        <div className="sidebar-projects-manage-actions">
          <button type="button" className="button danger" disabled={busy} onClick={onRemove}>
            {t("projects.remove")}
          </button>
          <button type="button" className="button" onClick={onCancel}>
            {t("projects.cancel")}
          </button>
        </div>
      </div>
    );
  }
  return (
    <div id={id} className="sidebar-projects-manage" role="group" aria-label={t("projects.manage", { name: label })}>
      <div className="sidebar-projects-manage-actions">
        <button type="button" className="button" onClick={() => onMode("rename")}>
          {t("projects.rename")}
        </button>
        {removable && (
          <button type="button" className="button" disabled={current} onClick={() => onMode("remove")}>
            {t("projects.remove")}
          </button>
        )}
      </div>
      {removable && current && <p>{t("projects.removeCurrent")}</p>}
    </div>
  );
}
