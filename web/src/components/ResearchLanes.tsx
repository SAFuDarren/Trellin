import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { InboxItem } from "../api/sections";
import { claimCode, statusText } from "../workbench/text";
import { laneGroups, obligationSegments, segmentSummary, visibleCards, type Segment } from "../workbench/claimBoard";
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
}) {
  const { t, i18n } = useTranslation();
  const [openCells, setOpenCells] = useState<ReadonlySet<string>>(new Set());
  const lanes = laneGroups(claims, i18n.language);
  const grid = { gridTemplateColumns: `minmax(150px, 180px) repeat(${columns.length}, minmax(200px, 1fr))` };
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
        <li key={claim.id} className="lane-row">
          <button
            type="button"
            className="lane-row-toggle"
            aria-expanded={expanded}
            aria-controls={expanded ? previewId : undefined}
            onClick={() => onToggleClaim(claim.id)}
          >
            <svg className="lane-row-mark" viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="12" cy="12" r="9" />
              <path d="m8.5 12.3 2.4 2.4 4.6-5" />
            </svg>
            <span className="lane-code">{code}</span>
            <span className="lane-row-title">{name}</span>
          </button>
          {expanded && renderPreview(claim.id, previewId)}
        </li>
      );
    }
    return (
      <li key={claim.id} className="lane-card" data-live={live > 0}>
        <button
          type="button"
          className="lane-card-toggle"
          aria-expanded={expanded}
          aria-controls={expanded ? previewId : undefined}
          onClick={() => onToggleClaim(claim.id)}
        >
          <span className="lane-card-head">
            <span className="lane-code">{code}</span>
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
        </button>
        {items && items.length > 0 && (
          <div className="lane-card-tags">
            <ClaimReviewLinks items={items} />
          </div>
        )}
        {expanded && renderPreview(claim.id, previewId)}
      </li>
    );
  }

  return (
    <div className="research-lanes" data-density={display.density}>
      <div className="lanes-scroll">
        <div className="lanes-head" style={grid}>
          <span />
          {columns.map((status) => (
            <span key={status} className="lanes-column" data-status={status}>
              <span className="lanes-dot" aria-hidden="true" />
              {statusText(t, status)}
              <span className="lanes-count">{claims.filter((claim) => claim.status === status).length}</span>
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
