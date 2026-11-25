import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { AgentWorkSummary } from "../api/agentWork";
import type { InboxItem } from "../api/sections";
import { AgentWorkDetail } from "../pages/AgentWorkPage";
import { ClaimPreview } from "../pages/ClaimsPage";
import { EvidenceReview, OtherItem } from "../pages/ReviewPage";
import { resolveWaiting, stepTarget, useWork, type PeekTarget } from "../shell/workContext";
import { obligationSegments, segmentSummary } from "../workbench/claimBoard";
import type { ClaimProgress } from "../workbench/claimProgress";
import { claimCode, statusText } from "../workbench/text";
import type { AtlasClaim } from "../workbench/types";
import { ClarificationReview } from "./ClarificationReview";
import { ClaimReviewLinks } from "./ClaimReviewLinks";
import { WorkbenchIcon } from "./Icons";
import { Segments, TrustBadge } from "./ResearchLanes";
import { workspaceActivityDisplay } from "./WorkspaceActivity";
import "../styles/work-panel.css";

// #326: open a waiting item or a claim large, beside the board, and move through the list without leaving it.

type PeekClaim = { id: string; title: string | null; status: string; domain: string | null; code?: string | null };

export type WorkPeekData = {
  session: string;
  claims: readonly PeekClaim[];
  atlas: ReadonlyMap<string, AtlasClaim>;
  progress: ReadonlyMap<string, ClaimProgress>;
  reviews: ReadonlyMap<string, readonly InboxItem[]>;
  running: ReadonlyMap<string, number>;
  tasks: AgentWorkSummary[];
  reviewWorkspace?: string;
  signer?: string;
  clarificationWorkspace?: string;
  onOpen: (claimId: string, nodeId?: string) => void;
};

function typing(target: EventTarget | null) {
  return target instanceof HTMLElement && Boolean(target.closest("input, textarea, select, [contenteditable=true]"));
}

export function WorkPeek(data: WorkPeekData) {
  const { t } = useTranslation();
  const work = useWork()!;
  const panel = useRef<HTMLElement>(null);
  const [notice, setNotice] = useState("");
  const target = work.peek!;
  const waiting = target.kind === "waiting" ? resolveWaiting(work.waiting, target) : null;
  const position =
    target.kind === "waiting"
      ? waiting && { index: waiting.index, total: work.waiting.length }
      : target.order.includes(target.id)
        ? { index: target.order.indexOf(target.id), total: target.order.length }
        : null;
  const previous = stepTarget(target, work.waiting, -1);
  const next = stepTarget(target, work.waiting, 1);
  const move = (to: PeekTarget | null) => {
    if (!to) return;
    setNotice("");
    work.setPeek(to);
  };
  const close = () => work.setPeek(null);

  useEffect(() => {
    panel.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey || typing(event.target)) return;
      if (document.querySelector(".floating-popover")) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      } else if (event.key === "j" || event.key === "J") {
        event.preventDefault();
        move(next);
      } else if (event.key === "k" || event.key === "K") {
        event.preventDefault();
        move(previous);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  let title = t("workPeek.title");
  let body: ReactNode;
  if (target.kind === "claim") {
    const claim = data.claims.find((entry) => entry.id === target.id);
    title = claim?.title || t("sidebar.untitled");
    body = claim ? <ClaimBody claim={claim} {...data} /> : <p className="page-empty">{t("workPeek.gone")}</p>;
  } else if (!waiting) {
    body = (
      <div className="work-peek-done" role="status">
        <strong>{t("workPeek.allDone")}</strong>
        <span>{t("workPeek.allDoneReason")}</span>
      </div>
    );
  } else {
    const { item } = waiting;
    const display = workspaceActivityDisplay(t, work.agents, item.row);
    title = display.title;
    const row = item.row;
    if (row.source === "review" && row.item.kind === "review_evidence" && row.item.claim_id) {
      body = (
        <EvidenceReview
          key={row.item.item_id}
          session={data.session}
          item={row.item}
          reviewWorkspace={data.reviewWorkspace}
          signer={data.signer}
          onOpen={data.onOpen}
          onDecided={setNotice}
        />
      );
    } else if (row.source === "review" && row.item.kind === "clarify_formalization" && data.clarificationWorkspace) {
      body = (
        <ClarificationReview
          key={row.item.item_id}
          session={data.session}
          workspace={data.clarificationWorkspace}
          id={row.item.item_id}
          onConfirmed={(kind) => setNotice(t(`clarifications.confirmed.${kind}`))}
        />
      );
    } else if (row.source === "review") {
      body = <OtherItem key={row.item.item_id} item={row.item} onOpen={data.onOpen} />;
    } else if (row.source === "work") {
      body = <AgentWorkDetail key={row.task.id} session={data.session} id={row.task.id} tasks={data.tasks} />;
    } else {
      body = (
        <div className="work-peek-run">
          <p className="work-peek-status">{[display.status, ...display.details].filter(Boolean).join(" · ")}</p>
          <div className="work-peek-actions">
            {row.claimId && (
              <button
                type="button"
                className="button primary"
                onClick={() => data.onOpen(row.claimId!, row.nodeId ?? undefined)}
              >
                {t("projectCanvas.openProof")}
              </button>
            )}
            <a className="button" href={display.url}>
              {t("workPanel.openPage")}
            </a>
          </div>
        </div>
      );
    }
  }

  return (
    <aside ref={panel} className="work-peek" role="dialog" aria-modal="false" aria-label={title} tabIndex={-1}>
      <header className="work-peek-head">
        <span className="work-peek-place">
          {target.kind === "waiting" ? t("workPanel.waitingLabel") : t("workPeek.claims")}
          {position && position.total > 0 && (
            <small>{t("workPeek.position", { index: position.index + 1, count: position.total })}</small>
          )}
        </span>
        <button
          type="button"
          className="icon-button"
          aria-label={t("workPeek.previous")}
          title={t("workPeek.previous")}
          aria-keyshortcuts="K"
          disabled={!previous}
          onClick={() => move(previous)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m18 15-6-6-6 6" />
          </svg>
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label={t("workPeek.next")}
          title={t("workPeek.next")}
          aria-keyshortcuts="J"
          disabled={!next}
          onClick={() => move(next)}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="m6 9 6 6 6-6" />
          </svg>
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label={t("workPeek.close")}
          title={t("workPeek.close")}
          aria-keyshortcuts="Escape"
          onClick={close}
        >
          <WorkbenchIcon name="cancel" />
        </button>
      </header>
      {notice && (
        <p className="work-peek-notice" role="status">
          {notice}
        </p>
      )}
      <div className="work-peek-body">{body}</div>
    </aside>
  );
}

function ClaimBody({ claim, ...data }: { claim: PeekClaim } & WorkPeekData) {
  const { t } = useTranslation();
  const work = useWork()!;
  const segments = obligationSegments(
    data.atlas.get(claim.id)?.nodes,
    data.progress.get(claim.id)?.obligations,
    (data.running.get(claim.id) ?? 0) > 0,
  );
  const { done, total, failed } = segmentSummary(segments);
  const reviews = data.reviews.get(claim.id);
  const pinned = work.isPinned(claim.id);
  return (
    <div className="work-peek-claim">
      <p className="work-peek-meta">
        <span className="lane-code">{claimCode(claim)}</span>
        <span>{statusText(t, claim.status)}</span>
        {claim.domain && <span>{claim.domain}</span>}
        <TrustBadge status={claim.status} progress={data.progress.get(claim.id)} />
      </p>
      <h2 className="work-peek-title">{claim.title || t("sidebar.untitled")}</h2>
      {total > 0 && (
        <p className="lane-progress">
          <Segments segments={segments} label={t("researchBoard.segmentsLabel", { done, total })} />
          <span>
            {failed
              ? t("researchBoard.progressFailed", { done, total, count: failed })
              : t("researchBoard.progress", { done, total })}
          </span>
        </p>
      )}
      {reviews && reviews.length > 0 && <ClaimReviewLinks items={reviews} />}
      <ClaimRelations claim={claim} {...data} />
      <div className="work-peek-actions">
        <button type="button" className="button" aria-pressed={pinned} onClick={() => work.togglePin(claim.id)}>
          {t(pinned ? "workPeek.unpin" : "workPeek.pin")}
        </button>
      </div>
      <ClaimPreview
        session={data.session}
        claimId={claim.id}
        id={`peek-preview-${claim.id}`}
        onOpen={() => data.onOpen(claim.id)}
      />
    </div>
  );
}

/** #342: the claims this claim relies on and the claims that rely on it, each opening here. */
function ClaimRelations({ claim, ...data }: { claim: PeekClaim } & WorkPeekData) {
  const { t } = useTranslation();
  const work = useWork()!;
  const progress = data.progress.get(claim.id);
  const groups = [
    { key: "uses", ids: progress?.uses ?? [] },
    { key: "usedBy", ids: progress?.usedBy ?? [] },
  ].filter(({ ids }) => ids.length > 0);
  if (!groups.length) return null;
  const order = work.peek?.kind === "claim" ? work.peek.order : [];
  return (
    <div className="work-peek-relations">
      {groups.map(({ key, ids }) => (
        <section key={key} aria-label={t(`workPeek.${key}`)}>
          <h3>{t(`workPeek.${key}`)}</h3>
          <ul role="list">
            {ids.map((id) => {
              const related = data.claims.find((entry) => entry.id === id);
              if (!related) return null;
              return (
                <li key={id}>
                  <button type="button" onClick={() => work.setPeek({ kind: "claim", id, order })}>
                    <span className="lane-code">{claimCode(related)}</span>
                    <span className="work-peek-relation-title">{related.title || t("sidebar.untitled")}</span>
                    <span className="work-peek-relation-status" data-status={related.status}>
                      {statusText(t, related.status)}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
