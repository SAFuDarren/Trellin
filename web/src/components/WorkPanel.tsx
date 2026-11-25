import { useContext, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { TeamAgent } from "../api/teamAgents";
import { currentEntry, currentRoute, entryHref, SPACES } from "../shell/spaces";
import { useWork, type WaitingItem, type WaitingKind } from "../shell/workContext";
import { href } from "../shell/route";
import { timeText } from "../workbench/text";
import { Chevron, WorkbenchIcon } from "./Icons";
import { SpaceCounts } from "./SectionLayout";
import { workspaceActivityDisplay } from "./WorkspaceActivity";
import "../styles/work-panel.css";

// #326: the Work panel — what waits for you, claim views, pinned claims and the record pages.

const WAITING_ROWS = 4;

function KindMark({ kind }: { kind: WaitingKind }) {
  const { t } = useTranslation();
  const label = t(`workPanel.kinds.${kind}`);
  return (
    <svg className="waiting-mark" data-kind={kind} viewBox="0 0 24 24" role="img" aria-label={label}>
      {kind === "review" && (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="m8.5 12.2 2.4 2.4 4.8-5" />
        </>
      )}
      {kind === "question" && (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="M9.6 9.4a2.5 2.5 0 1 1 3.4 2.3c-.6.3-1 .8-1 1.5v.3M12 16.6h.01" />
        </>
      )}
      {kind === "failed" && (
        <>
          <circle cx="12" cy="12" r="9" />
          <path d="m9.2 9.2 5.6 5.6M14.8 9.2l-5.6 5.6" />
        </>
      )}
      {kind === "delivery" && <path d="M4 8.5 12 4l8 4.5v7L12 20l-8-4.5zM4 8.5 12 13l8-4.5M12 13v7" />}
    </svg>
  );
}

function WaitingRow({
  item,
  agents,
  index,
}: {
  item: WaitingItem;
  agents: ReadonlyMap<string, TeamAgent>;
  index: number;
}) {
  const { t, i18n } = useTranslation();
  const work = useWork()!;
  const { title, details, url } = workspaceActivityDisplay(t, agents, item.row);
  const when = item.row.time ? timeText(i18n.language, item.row.time.at, true) : "";
  const kindLabel = t(`workPanel.kinds.${item.kind}`);
  // The first detail names the claim or Agent; a detail that repeats the kind adds nothing.
  const detail = details.find((entry) => entry && entry !== kindLabel && entry !== title);
  const meta = [kindLabel, detail, when].filter(Boolean).join(" · ");
  const open = work.peek?.kind === "waiting" && work.peek.id === item.id;
  return (
    <li className="waiting-row" data-open={open}>
      <button
        type="button"
        className="waiting-main"
        aria-haspopup="dialog"
        onClick={() => work.setPeek({ kind: "waiting", id: item.id, index })}
      >
        <KindMark kind={item.kind} />
        <span className="waiting-text">
          <strong title={title}>{title}</strong>
          <small title={meta}>{meta}</small>
        </span>
      </button>
      <span className="waiting-actions">
        <button
          type="button"
          className="waiting-action primary"
          onClick={() => work.setPeek({ kind: "waiting", id: item.id, index })}
        >
          {t("workPanel.handle")}
        </button>
        <a className="waiting-action" href={url}>
          {t("workPanel.openPage")}
        </a>
      </span>
    </li>
  );
}

const FOLDED_KEY = "trellin.work.panel.folded";

/** Panel sections folded by the researcher, kept on this device. */
function useFoldedSections() {
  const [folded, setFolded] = useState<ReadonlySet<string>>(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(FOLDED_KEY) ?? "[]");
      return new Set(Array.isArray(saved) ? saved.filter((key): key is string => typeof key === "string") : []);
    } catch {
      return new Set();
    }
  });
  function toggle(key: string) {
    const next = new Set(folded);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setFolded(next);
    try {
      window.localStorage.setItem(FOLDED_KEY, JSON.stringify([...next]));
    } catch {
      // Folding still applies to this window.
    }
  }
  return [folded, toggle] as const;
}

function SectionHead({
  id,
  label,
  open,
  onToggle,
  action,
}: {
  id: string;
  label: string;
  open: boolean;
  onToggle: () => void;
  action?: ReactNode;
}) {
  return (
    <div className="work-section-head">
      <button
        type="button"
        className="work-section-toggle"
        aria-expanded={open}
        aria-controls={`work-section-${id}`}
        onClick={onToggle}
      >
        <Chevron />
        <span id={`work-section-${id}-title`}>{label}</span>
      </button>
      {open && action}
    </div>
  );
}

const VIEW_ICONS: Record<string, string> = {
  all: "work",
  review: "review",
  running: "running",
  blocked: "blocked",
  advance: "advance",
};
const RECORD_ICONS: Record<string, string> = {
  inbox: "inbox",
  review: "review",
  "agent-tasks": "agent-work",
  library: "library",
};

/**
 * #326: the Work panel. Each section folds from its heading. A record page's own groups sit indented under its
 * entry, so moving between records changes only that entry's branch.
 */
export function WorkPanelSections({ pageGroups }: { pageGroups?: ReactNode }) {
  const { t } = useTranslation();
  const work = useWork();
  const spaceCounts = useContext(SpaceCounts);
  const [folded, toggle] = useFoldedSections();
  if (!work) return null;
  const route = currentRoute();
  const current = currentEntry(route)?.key;
  const records = SPACES.find((space) => space.id === "work")!.entries.filter((entry) => entry.key !== "claims");
  const viewCurrent = (target: string) =>
    route.section === "claims" && `#claims${route.sub ? "/" + route.sub : ""}` === target;
  const open = (key: string) => !folded.has(key);
  const branchKey = `records:${current}`;
  const currentIndex = records.findIndex((entry) => entry.key === current);
  const split = pageGroups && currentIndex >= 0 ? currentIndex + 1 : records.length;

  const record = (entry: (typeof records)[number]) => {
    const count = spaceCounts[entry.key];
    const hasBranch = Boolean(pageGroups) && entry.key === current;
    return (
      <div key={entry.key} className="work-record">
        <a className="nav-item" href={entryHref(entry)} aria-current={entry.key === current ? "page" : undefined}>
          <span className="nav-line">
            <WorkbenchIcon name={RECORD_ICONS[entry.key] ?? "details"} />
            <span className="label">{t(entry.labelKey)}</span>
            {count ? <span className="count">{count}</span> : null}
          </span>
        </a>
        {hasBranch && (
          <button
            type="button"
            className="work-branch-toggle"
            aria-expanded={open(branchKey)}
            aria-label={t(open(branchKey) ? "workPanel.foldGroups" : "workPanel.unfoldGroups", {
              name: t(entry.labelKey),
            })}
            onClick={() => toggle(branchKey)}
          >
            <Chevron />
          </button>
        )}
      </div>
    );
  };

  return (
    <>
      <section className="nav-group work-waiting" aria-labelledby="work-section-waiting-title">
        <SectionHead
          id="waiting"
          label={t("workPanel.waiting", { count: work.waiting.length })}
          open={open("waiting")}
          onToggle={() => toggle("waiting")}
          action={
            work.waiting.length > 0 && (
              <button
                type="button"
                className="work-section-link"
                onClick={() => work.setPeek({ kind: "waiting", id: work.waiting[0]!.id, index: 0 })}
              >
                {t("workPanel.handleAll")}
              </button>
            )
          }
        />
        {open("waiting") && (
          <div id="work-section-waiting">
            {work.waiting.length ? (
              <ul role="list">
                {work.waiting.slice(0, WAITING_ROWS).map((item, index) => (
                  <WaitingRow key={item.id} item={item} agents={work.agents} index={index} />
                ))}
              </ul>
            ) : (
              <p className="nav-empty">{t("workPanel.nothingWaiting")}</p>
            )}
            {work.waiting.length > WAITING_ROWS && (
              <a className="work-more" href={href("agent-work", "inbox")}>
                {t("workPanel.more", { count: work.waiting.length - WAITING_ROWS })}
              </a>
            )}
          </div>
        )}
      </section>

      <div className="nav-group work-views">
        <SectionHead id="views" label={t("workPanel.views")} open={open("views")} onToggle={() => toggle("views")} />
        {open("views") && (
          <div id="work-section-views">
            {work.views.map((view) => (
              <a
                key={view.key}
                className="nav-item"
                href={view.href}
                aria-current={viewCurrent(view.href) ? "page" : undefined}
              >
                <span className="nav-line">
                  <WorkbenchIcon name={VIEW_ICONS[view.key] ?? "work"} />
                  <span className="label">{t(`claimsPage.filter.${view.key}`)}</span>
                  <span className="count">{view.count}</span>
                </span>
              </a>
            ))}
          </div>
        )}
      </div>

      {work.pinned.length > 0 && (
        <div className="nav-group work-pins">
          <SectionHead id="pins" label={t("workPanel.pinned")} open={open("pins")} onToggle={() => toggle("pins")} />
          {open("pins") && (
            <div id="work-section-pins">
              {work.pinned.map((claim) => (
                <button
                  key={claim.id}
                  type="button"
                  className="nav-item work-pin"
                  onClick={() => work.setPeek({ kind: "claim", id: claim.id, order: work.pinned.map(({ id }) => id) })}
                >
                  <span className="nav-line">
                    <span className="work-pin-code">{claim.code}</span>
                    <span className="label">{claim.title || t("sidebar.untitled")}</span>
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="nav-group space-entries work-records">
        <SectionHead
          id="records"
          label={t("workPanel.records")}
          open={open("records")}
          onToggle={() => toggle("records")}
        />
        {open("records") && <div id="work-section-records">{records.slice(0, split).map(record)}</div>}
      </div>
      {open("records") && pageGroups && open(branchKey) && <div className="work-branch">{pageGroups}</div>}
      {open("records") && split < records.length && (
        <div className="nav-group space-entries work-records-rest">{records.slice(split).map(record)}</div>
      )}
    </>
  );
}
