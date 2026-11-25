import { useContext } from "react";
import { useTranslation } from "react-i18next";
import type { TeamAgent } from "../api/teamAgents";
import { currentEntry, currentRoute, entryHref, SPACES } from "../shell/spaces";
import { useWork, type WaitingItem, type WaitingKind } from "../shell/workContext";
import { href } from "../shell/route";
import { timeText } from "../workbench/text";
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

export function WorkPanelSections() {
  const { t } = useTranslation();
  const work = useWork();
  const spaceCounts = useContext(SpaceCounts);
  if (!work) return null;
  const route = currentRoute();
  const current = currentEntry(route)?.key;
  const records = SPACES.find((space) => space.id === "work")!.entries.filter((entry) => entry.key !== "claims");
  const viewCurrent = (target: string) =>
    route.section === "claims" && `#claims${route.sub ? "/" + route.sub : ""}` === target;
  return (
    <>
      <section className="nav-group work-waiting" aria-labelledby="work-waiting-title">
        <div className="work-section-head">
          <span id="work-waiting-title">{t("workPanel.waiting", { count: work.waiting.length })}</span>
          {work.waiting.length > 0 && (
            <button
              type="button"
              className="work-section-link"
              onClick={() => work.setPeek({ kind: "waiting", id: work.waiting[0]!.id, index: 0 })}
            >
              {t("workPanel.handleAll")}
            </button>
          )}
        </div>
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
      </section>

      <div className="nav-group work-views">
        <span>{t("workPanel.views")}</span>
        {work.views.map((view) => (
          <a
            key={view.key}
            className="nav-item"
            href={view.href}
            aria-current={viewCurrent(view.href) ? "page" : undefined}
          >
            <span className="nav-line">
              <span className="label">{t(`claimsPage.filter.${view.key}`)}</span>
              <span className="count">{view.count}</span>
            </span>
          </a>
        ))}
      </div>

      {work.pinned.length > 0 && (
        <div className="nav-group work-pins">
          <span>{t("workPanel.pinned")}</span>
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

      <div className="nav-group space-entries work-records">
        <span>{t("workPanel.records")}</span>
        {records.map((entry) => {
          const count = spaceCounts[entry.key];
          return (
            <a
              key={entry.key}
              className="nav-item"
              href={entryHref(entry)}
              aria-current={entry.key === current ? "page" : undefined}
            >
              <span className="nav-line">
                <span className="label">{t(entry.labelKey)}</span>
                {count ? <span className="count">{count}</span> : null}
              </span>
            </a>
          );
        })}
      </div>
    </>
  );
}
