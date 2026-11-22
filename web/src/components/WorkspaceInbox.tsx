import { useRef, useState, type KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import type { TeamAgent } from "../api/teamAgents";
import type { TriageMark, WorkspaceInboxState } from "../shell/workspaceInbox";
import { timeText } from "../workbench/text";
import { workspaceActivityDisplay } from "./WorkspaceActivity";
import "../styles/workspace-inbox.css";

const FILTERS = ["all", "unread", "archived"] as const;
type Filter = (typeof FILTERS)[number];
type InboxRow = WorkspaceInboxState["rows"][number];

export function WorkspaceInbox({
  inbox,
  agents,
  loading,
  unavailable,
  onOpenProof,
}: {
  inbox: WorkspaceInboxState;
  agents: ReadonlyMap<string, TeamAgent>;
  loading: boolean;
  unavailable: boolean;
  onOpenProof: (claimId: string, nodeId?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const [filter, setFilter] = useState<Filter>("all");
  const [selectedId, setSelectedId] = useState<string>();
  const toolbar = useRef<HTMLDivElement>(null);
  const list = useRef<HTMLUListElement>(null);
  const preview = useRef<HTMLElement>(null);
  const undoButton = useRef<HTMLButtonElement>(null);
  const visible = inbox.rows.filter(({ mark }) => (filter === "all" ? mark !== "archived" : mark === filter));
  const selected = visible.find(({ record }) => record.id === selectedId);
  const summary = selected && workspaceActivityDisplay(t, agents, selected.record);

  function changeMark(row: InboxRow, mark: TriageMark) {
    inbox.mark(row.record, mark);
    const staysVisible = filter === "all" ? mark !== "archived" : mark === filter;
    if (staysVisible) return;
    if (row.record.id === selectedId) setSelectedId(undefined);
    const index = visible.indexOf(row);
    requestAnimationFrame(() => {
      const buttons = list.current?.querySelectorAll("button");
      (buttons?.[index] ?? buttons?.[index - 1] ?? undoButton.current)?.focus();
    });
  }

  function undo() {
    const id = inbox.undoId;
    inbox.undo();
    requestAnimationFrame(() => {
      const row = Array.from(list.current?.children ?? []).find((item) => item.getAttribute("data-inbox-row") === id);
      const destination =
        row?.querySelector("button") ??
        toolbar.current?.querySelector<HTMLButtonElement>('button[aria-pressed="true"]');
      destination?.focus();
    });
  }

  function handleKey(event: KeyboardEvent<HTMLElement>) {
    const target = event.target;
    if (
      event.nativeEvent.isComposing ||
      event.altKey ||
      event.ctrlKey ||
      event.metaKey ||
      event.shiftKey ||
      !(target instanceof HTMLElement) ||
      target.closest("input,select,textarea,[contenteditable=true]")
    )
      return;
    const id = target.closest("[data-inbox-row]")?.getAttribute("data-inbox-row");
    const row = visible.find(({ record }) => record.id === id) ?? selected;
    if (event.key === "z" && inbox.undoId) {
      event.preventDefault();
      undo();
    } else if (row && row.mark !== "archived") {
      if (event.key === "e") {
        event.preventDefault();
        changeMark(row, "archived");
      } else if (event.key === "r" && row.mark === "unread") {
        event.preventDefault();
        changeMark(row, "read");
      }
    }
  }

  if (!inbox.enabled) {
    return (
      <p className="page-empty" role="status">
        {t(loading ? "common.loading" : "agentInbox.unavailable")}
      </p>
    );
  }

  return (
    <section className="page-body workspace-inbox" aria-label={t("agentInbox.title")} onKeyDown={handleKey}>
      <div className="inbox-toolbar" ref={toolbar}>
        <div className="segmented" role="group" aria-label={t("agentInbox.filter")}>
          {FILTERS.map((item) => (
            <button type="button" key={item} aria-pressed={filter === item} onClick={() => setFilter(item)}>
              {t(`agentInbox.filters.${item}`)}
            </button>
          ))}
        </div>
        <button
          ref={undoButton}
          type="button"
          className="button"
          aria-keyshortcuts="Z"
          disabled={!inbox.undoId}
          onClick={undo}
        >
          {t("agentInbox.undo")}
        </button>
      </div>
      <p className="inbox-note">{t("agentInbox.windowNote")}</p>
      {unavailable && <p role="status">{t("agentDock.activityUnavailable")}</p>}
      <div className="inbox-content" data-selected={Boolean(selected)}>
        {visible.length > 0 ? (
          <ul className="inbox-list" ref={list}>
            {visible.map(({ record, mark }) => {
              const { title, status, details } = workspaceActivityDisplay(t, agents, record);
              return (
                <li key={record.id} data-inbox-row={record.id} data-mark={mark}>
                  <button
                    type="button"
                    aria-pressed={selectedId === record.id}
                    onClick={() => {
                      setSelectedId(record.id);
                      requestAnimationFrame(() => preview.current?.scrollIntoView({ block: "nearest" }));
                    }}
                  >
                    <strong>{title}</strong>
                    <small>
                      {[status, ...details].filter(Boolean).join(" · ")}
                      {mark === "unread" && <span className="sr-only"> {t("agentInbox.unread")}</span>}
                    </small>
                  </button>
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="page-note" role="status">
            {t(loading ? "common.loading" : `agentInbox.empty.${filter}`)}
          </p>
        )}
        {selected && summary && (
          <aside ref={preview} className="inbox-record" aria-labelledby="inbox-record-title">
            <div>
              <h3 id="inbox-record-title">{summary.title}</h3>
              <p>{[summary.status, ...summary.details].filter(Boolean).join(" · ")}</p>
              {selected.record.time && (
                <time dateTime={selected.record.time.at}>{timeText(i18n.language, selected.record.time.at, true)}</time>
              )}
            </div>
            {selected.record.source === "review" && selected.record.item.question && (
              <p className="inbox-question">{selected.record.item.question}</p>
            )}
            <div className="choices">
              {selected.record.source === "job" && selected.record.claimId ? (
                <button
                  type="button"
                  className="button"
                  onClick={() => onOpenProof(selected.record.claimId!, selected.record.nodeId ?? undefined)}
                >
                  {t("projectCanvas.openProof")}
                </button>
              ) : (
                <a className="button" href={summary.url}>
                  {t("agentInbox.openRecord")}
                </a>
              )}
              {selected.mark === "archived" ? (
                <button type="button" className="button" onClick={() => changeMark(selected, "read")}>
                  {t("agentInbox.restore")}
                </button>
              ) : (
                <>
                  <button
                    type="button"
                    className="button"
                    aria-keyshortcuts={selected.mark === "unread" ? "R" : undefined}
                    onClick={() => changeMark(selected, selected.mark === "unread" ? "read" : "unread")}
                  >
                    {t(selected.mark === "unread" ? "agentInbox.markRead" : "agentInbox.markUnread")}
                  </button>
                  <button
                    type="button"
                    className="button"
                    aria-keyshortcuts="E"
                    onClick={() => changeMark(selected, "archived")}
                  >
                    {t("agentInbox.archive")}
                  </button>
                </>
              )}
            </div>
          </aside>
        )}
      </div>
    </section>
  );
}
