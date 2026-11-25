import { Fragment, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, type ClaimsResponse } from "../api/client";
import { useClaims, type InboxItem } from "../api/sections";
import { ClaimProgressSummary, claimProgressText } from "../components/ClaimProgress";
import { ClaimReviewLinks } from "../components/ClaimReviewLinks";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { Spin } from "../components/Icons";
import { BoardControls, BoardFilterChips, useBoardDisplay, useCollapsedLanes } from "../components/BoardControls";
import { ResearchLanes } from "../components/ResearchLanes";
import { WorkStatusColumn, WorkTable } from "../components/WorkDisplay";
import { WorkSelectionArea, WorkSelectionCheckbox, useWorkItemSelection } from "../components/WorkItemSelection";
import { href } from "../shell/route";
import type { ClaimProgress } from "../workbench/claimProgress";
import { boardColumns, matchesSearch } from "../workbench/claimBoard";
import { claimCode, statusText } from "../workbench/text";
import type { AtlasClaim, GraphSnapshot } from "../workbench/types";

const FILTERS = ["all", "review", "blocked", "running"] as const;

function ClaimPreview({
  session,
  claimId,
  id,
  onOpen,
}: {
  session: string;
  claimId: string;
  id: string;
  onOpen: () => void;
}) {
  const { t } = useTranslation();
  const graph = useQuery({
    queryKey: ["graph", claimId],
    queryFn: ({ signal }) => api<GraphSnapshot>(session, "/api/graph?claim=" + encodeURIComponent(claimId), { signal }),
  });
  const goal = graph.data?.nodes.find((node) => node.kind === "goal");
  return (
    <div className="board-preview" id={id}>
      {graph.isPending ? (
        <p role="status">{t("common.loading")}</p>
      ) : graph.isError ? (
        <p role="alert">{graph.error.message}</p>
      ) : goal ? (
        <>
          {goal.statement && <p>{goal.statement}</p>}
          {goal.formula && goal.formula !== goal.statement && goal.formula !== goal.formalization && (
            <p className="math">{goal.formula}</p>
          )}
          {goal.formalization ? (
            <>
              <span className="field-label">{t("detail.lean")}</span>
              <pre className="code-block">{goal.formalization}</pre>
            </>
          ) : (
            <p>{t("claimsPage.noLean")}</p>
          )}
        </>
      ) : (
        <p>{t("claimsPage.noStatement")}</p>
      )}
      <button type="button" className="button" onClick={onOpen}>
        {t("claimsPage.openWorkbench")}
      </button>
    </div>
  );
}

export function ClaimsPage({
  session,
  sub,
  onOpen,
  claimProgress,
  claimReviews,
  atlasClaims,
  running,
  onNewClaim,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string) => void;
  claimProgress: ReadonlyMap<string, ClaimProgress>;
  claimReviews: ReadonlyMap<string, readonly InboxItem[]>;
  atlasClaims?: AtlasClaim[];
  running: ReadonlyMap<string, number>;
  /** #326: New claim opens the project's Add theorem entry. */
  onNewClaim?: () => void;
}) {
  const { t } = useTranslation();
  const [expandedClaim, setExpandedClaim] = useState<string | null>(null);
  const [display, setDisplay] = useBoardDisplay();
  const [collapsed, toggleLane] = useCollapsedLanes();
  const [search, setSearch] = useState("");
  const [statuses, setStatuses] = useState<ReadonlySet<string>>(new Set());
  const layout = display.layout;
  const claims = useClaims(session);
  const counts = new Map((atlasClaims ?? []).map((claim) => [claim.id, claim]));
  const list = claims.data?.claims ?? [];
  const domains = [...new Set(list.map((claim) => claim.domain || t("sidebar.uncategorized")))].sort();
  const [filter, domain] = sub.startsWith("domain:")
    ? ["all", sub.slice(7)]
    : [FILTERS.includes(sub as never) ? sub : "all", ""];
  const shown = list.filter((claim) => {
    const record = counts.get(claim.id);
    if (domain && (claim.domain || t("sidebar.uncategorized")) !== domain) return false;
    if (statuses.size && !statuses.has(claim.status)) return false;
    if (!matchesSearch(claim, claimCode(claim), search)) return false;
    if (filter === "review") return Boolean(claimReviews.get(claim.id)?.length);
    if (filter === "blocked") return (record?.blocked ?? 0) > 0;
    if (filter === "running") return (running.get(claim.id) ?? 0) > 0;
    return true;
  });
  const filterCount = (key: (typeof FILTERS)[number]) =>
    list.filter((claim) =>
      key === "all"
        ? true
        : key === "review"
          ? Boolean(claimReviews.get(claim.id)?.length)
          : key === "blocked"
            ? (counts.get(claim.id)?.blocked ?? 0) > 0
            : (running.get(claim.id) ?? 0) > 0,
    ).length;
  const columns = boardColumns(list.map((claim) => claim.status));
  const routeFor = (nextDomain: string) =>
    href("claims", nextDomain ? "domain:" + nextDomain : filter === "all" ? "" : filter);
  const filters = {
    domain,
    domains,
    statuses,
    allStatuses: columns,
    search,
  };
  const filterActions = {
    onDomain: (next: string) => {
      window.location.hash = routeFor(next);
    },
    onToggleStatus: (status: string) => {
      const next = new Set(statuses);
      if (next.has(status)) next.delete(status);
      else next.add(status);
      setStatuses(next);
    },
    onSearch: setSearch,
    onClear: () => {
      setSearch("");
      setStatuses(new Set());
      if (domain) window.location.hash = routeFor("");
    },
  };
  const title = domain || t(`claimsPage.filter.${filter}`);
  const selectionScope = JSON.stringify([session, sub, layout]);
  const selection = useWorkItemSelection(selectionScope, layout === "board" ? [] : shown.map(({ id }) => id));

  function renderClaim(claim: ClaimsResponse["claims"][number]) {
    const record = counts.get(claim.id);
    const progress = claimProgress.get(claim.id);
    const summary = claimProgressText(t, progress, claim.status);
    const live = running.get(claim.id) ?? 0;
    const repeatColumn =
      layout === "board" &&
      (summary.label === statusText(t, claim.status) ||
        (claim.status === "awaiting_interpretation_review" && summary.state === "decision"));
    const reviewItems = claimReviews.get(claim.id);
    const showReview = Boolean(reviewItems?.length);
    const repeatCount = (showReview && summary.state === "review") || (live > 0 && summary.state === "running");
    const showBlocked = Boolean(record?.blocked) && summary.state !== "decision" && summary.state !== "recheck";
    const expanded = expandedClaim === claim.id;
    const previewId = "claim-preview-" + claim.id;
    const toggleProps = {
      "aria-expanded": expanded,
      "aria-controls": expanded ? previewId : undefined,
      onClick: () => setExpandedClaim(expanded ? null : claim.id),
    };
    const name = claim.title || t("sidebar.untitled");
    const claimDomain = claim.domain || t("sidebar.uncategorized");
    const obligations = progress?.obligations;
    const bar = obligations && obligations.total > 0 && (
      <span className="progress" aria-hidden="true">
        <span className="done" style={{ width: `${Math.round((obligations.verified / obligations.total) * 100)}%` }} />
      </span>
    );
    const progressSummary = <ClaimProgressSummary {...summary} showState={!repeatColumn && !repeatCount} />;
    const tags = (showReview || showBlocked || live > 0) && (
      <div className="tags board-card-tags">
        {live > 0 && (
          <span className="tag" data-tone="live">
            <Spin />
            {t("claimsPage.running", { count: live })}
          </span>
        )}
        {reviewItems && <ClaimReviewLinks items={reviewItems} />}
        {showBlocked && (
          <span className="tag" data-tone="bad">
            {t("sidebar.blocked", { count: record!.blocked })}
          </span>
        )}
      </div>
    );
    const preview = expanded && (
      <ClaimPreview session={session} claimId={claim.id} id={previewId} onOpen={() => onOpen(claim.id)} />
    );

    const checkbox = <WorkSelectionCheckbox id={claim.id} title={name} selection={selection} />;
    const selected = selection.contains(claim.id);
    if (layout === "table") {
      return (
        <Fragment key={claim.id}>
          <tr data-expanded={expanded} data-work-selected={selected}>
            <td className="work-selection-cell">{checkbox}</td>
            <th scope="row">
              <button type="button" className="work-display-name" {...toggleProps}>
                {name}
              </button>
            </th>
            <td className="muted">{claimDomain}</td>
            <td>
              <div className="work-display-progress">
                {progressSummary}
                {tags}
              </div>
            </td>
          </tr>
          {preview && (
            <tr className="work-display-detail">
              <td colSpan={4}>{preview}</td>
            </tr>
          )}
        </Fragment>
      );
    }

    const content = (
      <>
        <button type="button" className="board-card-toggle" {...toggleProps}>
          <span className="title">{name}</span>
          {bar}
          {progressSummary}
          <span className="meta">{claimDomain}</span>
        </button>
        {tags}
        {preview}
      </>
    );
    return layout === "list" ? (
      <li className="board-card work-selection-row" key={claim.id} data-live={live > 0} data-work-selected={selected}>
        {checkbox}
        <div className="work-selection-row-content">{content}</div>
      </li>
    ) : (
      <article className="board-card" key={claim.id} data-live={live > 0}>
        {content}
      </article>
    );
  }

  return (
    <SectionLayout
      space="work"
      title={t("rail.claims")}
      groups={[
        {
          entries: FILTERS.map((key) => ({
            key,
            label: t(`claimsPage.filter.${key}`),
            href: href("claims", key === "all" ? "" : key),
            current: !domain && filter === key,
            count: filterCount(key),
            countTone: key === "running" && filterCount(key) ? "live" : undefined,
          })),
        },
        {
          name: t("claimsPage.domains"),
          entries: domains.map((name) => ({
            key: name,
            label: name,
            href: href("claims", "domain:" + name),
            current: domain === name,
            count: list.filter((claim) => (claim.domain || t("sidebar.uncategorized")) === name).length,
          })),
        },
      ]}
    >
      <PageHead
        title={title}
        note={
          !claims.isSuccess
            ? undefined
            : shown.length === list.length
              ? t("researchBoard.countAll", { count: list.length })
              : t("researchBoard.countFiltered", { count: shown.length, total: list.length })
        }
        actions={
          <BoardControls
            filters={filters}
            display={display}
            onDisplay={setDisplay}
            onNew={onNewClaim}
            {...filterActions}
          />
        }
      />
      <BoardFilterChips filters={filters} {...filterActions} />
      {claims.isError ? (
        <Empty>{claims.error.message}</Empty>
      ) : shown.length === 0 ? (
        <Empty>{claims.isPending ? t("common.loading") : t("claimsPage.none")}</Empty>
      ) : layout === "lanes" ? (
        <ResearchLanes
          claims={shown}
          columns={columns}
          display={display}
          collapsed={collapsed}
          onToggleLane={toggleLane}
          atlas={counts}
          progress={claimProgress}
          reviews={claimReviews}
          running={running}
          expandedClaim={expandedClaim}
          onToggleClaim={(id) => setExpandedClaim(expandedClaim === id ? null : id)}
          renderPreview={(id, previewId) => (
            <ClaimPreview session={session} claimId={id} id={previewId} onOpen={() => onOpen(id)} />
          )}
        />
      ) : layout !== "board" ? (
        <WorkSelectionArea key={selectionScope} label={title} selection={selection}>
          {layout === "table" ? (
            <WorkTable
              label={title}
              columns={[t("workDisplay.claim"), t("workDisplay.domain"), t("workDisplay.progress")]}
              selectionLabel={t("workSelection.column")}
            >
              {shown.map(renderClaim)}
            </WorkTable>
          ) : (
            <div className="page-body">
              <ul className="work-display-list" role="list" aria-label={title}>
                {shown.map(renderClaim)}
              </ul>
            </div>
          )}
        </WorkSelectionArea>
      ) : (
        <div className="board">
          {columns.map((status) => {
            const cards = shown.filter((claim) => claim.status === status);
            if (!cards.length && !["open", "proved"].includes(status)) return null;
            return (
              <WorkStatusColumn
                key={status}
                label={statusText(t, status)}
                count={cards.length}
                tone={
                  status === "proved"
                    ? "ok"
                    : status === "needs_recheck" || status === "refuted"
                      ? "bad"
                      : status === "open"
                        ? "none"
                        : "warn"
                }
              >
                {cards.map(renderClaim)}
              </WorkStatusColumn>
            );
          })}
        </div>
      )}
    </SectionLayout>
  );
}
