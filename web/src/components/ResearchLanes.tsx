import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { InboxItem } from "../api/sections";
import { claimCode, statusText } from "../workbench/text";
import {
  laneGroups,
  obligationSegments,
  segmentSummary,
  trustMark,
  visibleCards,
  type Segment,
} from "../workbench/claimBoard";
import type { ClaimProgress } from "../workbench/claimProgress";
import type { AtlasClaim } from "../workbench/types";
import type { BoardDisplay } from "./BoardControls";
import { ClaimReviewLinks } from "./ClaimReviewLinks";
import { Chevron } from "./Icons";
import "../styles/research-board.css";

// #326: claims in rows by domain and columns by status. A row folds to one line of counts.

type BoardClaim = { id: string; title: string | null; status: string; domain: string | null; code?: string | null };

const MAX_LANE_SEGMENTS = 24;

export function Segments({ segments, label }: { segments: readonly Segment[]; label?: string }) {
  if (!segments.length) return null;
  return (
    <span
      className="segments"
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    >
      {segments.map((segment, index) => (
        <span key={index} data-segment={segment} />
      ))}
    </span>
  );
}

/** #342: how a claim was established, with a reviewed mark when its decisive evidence was approved. */
export function TrustBadge({ status, progress }: { status: string; progress: ClaimProgress | undefined }) {
  const { t } = useTranslation();
  const trust = trustMark(status, progress?.assurance);
  if (!trust) return null;
  const mark = t(`researchBoard.trust.${trust.mark}`);
  const title = t(`researchBoard.trustTitle.${trust.mark}`);
  return (
    <span
      className="trust-badge"
      data-mark={trust.mark}
      data-reviewed={trust.reviewed}
      title={trust.reviewed ? t("researchBoard.trustTitleReviewed", { title }) : title}
    >
      {mark}
      {trust.reviewed && (
        <>
          <svg className="trust-check" viewBox="0 0 24 24" aria-hidden="true">
            <path d="m5.5 12.5 4 4 9-9.5" />
          </svg>
          <span className="sr-only">{t("researchBoard.trustReviewed")}</span>
        </>
      )}
    </span>
  );
}

/** #342: which claims the claim relies on, by code, and how many rely on it. */
export function DependencyLine({
  progress,
  codeOf,
  usedByOnly = false,
}: {
  progress: ClaimProgress | undefined;
  codeOf: (claimId: string) => string;
  /** A one-line proved row shows only how many claims rely on it. */
  usedByOnly?: boolean;
}) {
  const { t } = useTranslation();
  const uses = usedByOnly ? [] : (progress?.uses ?? []);
  const usedBy = progress?.usedBy?.length ?? 0;
  if (!uses.length && !usedBy) return null;
  return (
    <span className="lane-deps">
      {uses.length > 0 && <span>{t("researchBoard.uses", { codes: uses.map(codeOf).join(", ") })}</span>}
      {usedBy > 0 && <span>{t("researchBoard.usedBy", { count: usedBy })}</span>}
    </span>
  );
}

type Relation = "self" | "uses" | "usedBy" | "other";

export function ResearchLanes({
  claims,
  columns,
  display,
  collapsed,
  onToggleLane,
  atlas,
  progress,
  reviews,
  running,
  expandedClaim,
  onToggleClaim,
  renderPreview,
  opensPanel = false,
  codes,
}: {
  claims: readonly BoardClaim[];
  columns: readonly string[];
  display: BoardDisplay;
  collapsed: ReadonlySet<string>;
  onToggleLane: (lane: string) => void;
  atlas: ReadonlyMap<string, AtlasClaim>;
  progress: ReadonlyMap<string, ClaimProgress>;
  reviews: ReadonlyMap<string, readonly InboxItem[]>;
  running: ReadonlyMap<string, number>;
  expandedClaim: string | null;
  onToggleClaim: (claimId: string) => void;
  renderPreview: (claimId: string, id: string) => ReactNode;
  /** #326: a card opens the side panel instead of a preview in place. */
  opensPanel?: boolean;
  /** Codes of claims the board may not show, for the dependency line. */
  codes?: ReadonlyMap<string, string>;
}) {
  const { t, i18n } = useTranslation();
  const [openCells, setOpenCells] = useState<ReadonlySet<string>>(new Set());
  // The edge under the pinned domain names shows only once cards have slid beneath it.
  const [scrolled, setScrolled] = useState(false);
  const board = useRef<HTMLDivElement>(null);
  // #345: the floating window covers the board's right side, so the claim it shows is scrolled beside it.
  useEffect(() => {
    if (!opensPanel || !expandedClaim) return;
    board.current
      ?.querySelector(`[data-claim="${CSS.escape(expandedClaim)}"]`)
      ?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [opensPanel, expandedClaim]);
  // #342: pointing at or selecting a card traces what it relies on and what relies on it; the rest dims.
  const [pointed, setPointed] = useState<string | null>(null);
  const anchor = pointed ?? expandedClaim;
  const anchorUses = new Set(anchor ? (progress.get(anchor)?.uses ?? []) : []);
  const anchorUsedBy = new Set(anchor ? (progress.get(anchor)?.usedBy ?? []) : []);
  const tracing = anchorUses.size + anchorUsedBy.size > 0;
  const relationOf = (claimId: string): Relation =>
    claimId === anchor ? "self" : anchorUses.has(claimId) ? "uses" : anchorUsedBy.has(claimId) ? "usedBy" : "other";
  const boardCodes = new Map(claims.map((claim) => [claim.id, claimCode(claim)]));
  const codeOf = (claimId: string) => boardCodes.get(claimId) ?? codes?.get(claimId) ?? claimCode({ id: claimId });
  const tracedProps = (claimId: string) => ({
    "data-relation": tracing ? relationOf(claimId) : undefined,
    onMouseEnter: () => setPointed(claimId),
    onMouseLeave: () => setPointed((current) => (current === claimId ? null : current)),
    onFocus: () => setPointed(claimId),
    onBlur: () => setPointed((current) => (current === claimId ? null : current)),
  });
  const relationNote = (claimId: string) => {
    const relation = tracing ? relationOf(claimId) : "other";
    return relation === "uses" || relation === "usedBy" ? (
      <span className="sr-only">{t(`researchBoard.relation.${relation}`)}</span>
    ) : null;
  };
  const lanes = laneGroups(claims, i18n.language);
  // Columns share the width up to a comfortable card width, so a wide window leaves space rather than stretching cards.
  const grid = { gridTemplateColumns: `var(--lane-label-w) repeat(${columns.length}, minmax(200px, 340px))` };
  const segmentsOf = (claim: BoardClaim) =>
    obligationSegments(
      atlas.get(claim.id)?.nodes,
      progress.get(claim.id)?.obligations,
      (running.get(claim.id) ?? 0) > 0,
    );

  function card(claim: BoardClaim) {
    const name = claim.title || t("sidebar.untitled");
    const code = claimCode(claim);
    const live = running.get(claim.id) ?? 0;
    const items = reviews.get(claim.id);
    const segments = segmentsOf(claim);
    const { done, total, failed } = segmentSummary(segments);
    const expanded = expandedClaim === claim.id;
    const previewId = "lane-preview-" + claim.id;
    const asRow = display.proved === "rows" && claim.status === "proved";
    if (asRow) {
      return (
        <li key={claim.id} className="lane-row" data-claim={claim.id} data-open={expanded} {...tracedProps(claim.id)}>
          <button
            type="button"
            className="lane-row-toggle"
            aria-expanded={expanded}
            aria-haspopup={opensPanel ? "dialog" : undefined}
            aria-controls={expanded && !opensPanel ? previewId : undefined}
            onClick={() => onToggleClaim(claim.id)}
          >
            <svg className="lane-row-mark" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="m8.5 12.3 2.4 2.4 4.6-5" />
            </svg>
            <span className="lane-code">{code}</span>
            <span className="lane-row-title">{name}</span>
            <DependencyLine progress={progress.get(claim.id)} codeOf={codeOf} usedByOnly />
            <TrustBadge status={claim.status} progress={progress.get(claim.id)} />
            {relationNote(claim.id)}
          </button>
          {expanded && !opensPanel && renderPreview(claim.id, previewId)}
        </li>
      );
    }
    return (
      <li
        key={claim.id}
        className="lane-card"
        data-claim={claim.id}
        data-live={live > 0}
        data-open={expanded}
        {...tracedProps(claim.id)}
      >
        <button
          type="button"
          className="lane-card-toggle"
          aria-expanded={expanded}
          aria-haspopup={opensPanel ? "dialog" : undefined}
          aria-controls={expanded && !opensPanel ? previewId : undefined}
          onClick={() => onToggleClaim(claim.id)}
        >
          <span className="lane-card-head">
            <span className="lane-code">{code}</span>
            <TrustBadge status={claim.status} progress={progress.get(claim.id)} />
            {live > 0 && (
              <span className="lane-live">
                <span aria-hidden="true" />
                {t("claimsPage.running", { count: live })}
              </span>
            )}
          </span>
          <span className="lane-title">{name}</span>
          {total > 0 && (
            <span className="lane-progress">
              <Segments segments={segments} />
              <span>
                {failed
                  ? t("researchBoard.progressFailed", { done, total, count: failed })
                  : t("researchBoard.progress", { done, total })}
              </span>
            </span>
          )}
          <DependencyLine progress={progress.get(claim.id)} codeOf={codeOf} />
          {relationNote(claim.id)}
        </button>
        {items && items.length > 0 && (
          <div className="lane-card-tags">
            <ClaimReviewLinks items={items} />
          </div>
        )}
        {expanded && !opensPanel && renderPreview(claim.id, previewId)}
      </li>
    );
  }

  return (
    <div
      ref={board}
      className="research-lanes"
      data-density={display.density}
      data-scrolled={scrolled}
      data-tracing={tracing}
      onScroll={(event) => setScrolled(event.currentTarget.scrollLeft > 0)}
    >
      <div className="lanes-scroll">
        <div className="lanes-head" style={grid}>
          <span className="lanes-corner" />
          {columns.map((status) => (
            <span key={status} className="lanes-column" data-status={status}>
              <span className="lanes-dot" aria-hidden="true" />
              <span>
                {statusText(t, status)}{" "}
                <span className="lanes-count">{claims.filter((claim) => claim.status === status).length}</span>
              </span>
            </span>
          ))}
        </div>
        {lanes.map((lane) => {
          const label = lane.key || t("sidebar.uncategorized");
          const isCollapsed = collapsed.has(lane.key);
          const proved = lane.claims.filter((claim) => claim.status === "proved").length;
          const live = lane.claims.filter((claim) => (running.get(claim.id) ?? 0) > 0).length;
          const laneSegments: Segment[] = lane.claims
            .slice(0, MAX_LANE_SEGMENTS)
            .map((_, index) => (index < proved ? "done" : "open"));
          return (
            <section key={lane.key} className="lane" aria-label={label}>
              <div className="lane-grid" data-collapsed={isCollapsed} style={grid}>
                <button
                  type="button"
                  className="lane-toggle"
                  aria-expanded={!isCollapsed}
                  onClick={() => onToggleLane(lane.key)}
                >
                  <Chevron />
                  <span>
                    <strong>{label}</strong>
                    <small>
                      {t("researchBoard.laneSummary", { count: lane.claims.length, proved })}
                      {live > 0 && t("researchBoard.laneRunning", { count: live })}
                    </small>
                    <Segments segments={laneSegments} />
                  </span>
                </button>
                {columns.map((status) => {
                  const cards = lane.claims.filter((claim) => claim.status === status);
                  if (isCollapsed) {
                    return (
                      <span key={status} className="lane-folded" data-status={status}>
                        <strong>{cards.length}</strong>
                        <span className="lane-folded-bar" aria-hidden="true">
                          {Array.from({ length: Math.min(cards.length, 6) }, (_, index) => (
                            <span key={index} />
                          ))}
                        </span>
                      </span>
                    );
                  }
                  const cellKey = lane.key + "\u0000" + status;
                  const { shown, hidden } = visibleCards(cards, display.perCell, openCells.has(cellKey));
                  return (
                    <div
                      key={status}
                      className="lane-cell"
                      role="group"
                      aria-label={t("researchBoard.cell", { lane: label, status: statusText(t, status) })}
                    >
                      <ul role="list">{shown.map(card)}</ul>
                      {hidden > 0 && (
                        <button
                          type="button"
                          className="lane-more"
                          onClick={() => setOpenCells(new Set([...openCells, cellKey]))}
                        >
                          {t("researchBoard.showMore", { count: hidden })}
                        </button>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          );
        })}
      </div>
    </div>
  );
}
