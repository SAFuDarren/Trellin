import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { useClaims, type InboxItem } from "../api/sections";
import { ClaimProgressSummary, claimProgressText } from "../components/ClaimProgress";
import { ClaimReviewLinks } from "../components/ClaimReviewLinks";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { Spin } from "../components/Icons";
import { href } from "../shell/route";
import type { ClaimProgress } from "../workbench/claimProgress";
import { statusText } from "../workbench/text";
import type { AtlasClaim, GraphSnapshot } from "../workbench/types";

const COLUMNS = ["open", "awaiting_interpretation_review", "needs_recheck", "proved", "refuted"] as const;
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
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string) => void;
  claimProgress: ReadonlyMap<string, ClaimProgress>;
  claimReviews: ReadonlyMap<string, readonly InboxItem[]>;
  atlasClaims?: AtlasClaim[];
  running: ReadonlyMap<string, number>;
}) {
  const { t } = useTranslation();
  const [expandedClaim, setExpandedClaim] = useState<string | null>(null);
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
  const columns = [
    ...COLUMNS,
    ...new Set(list.map((claim) => claim.status).filter((status) => !(COLUMNS as readonly string[]).includes(status))),
  ];
  return (
    <SectionLayout
      title={t("rail.claims")}
      groups={[
        {
          entries: [{ key: "agent-work", label: t("agentWork.title"), href: href("claims", "agent-work") }],
        },
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
      <PageHead title={domain || t(`claimsPage.filter.${filter}`)} note={t("claimsPage.byStatus")} />
      {claims.isError ? (
        <Empty>{claims.error.message}</Empty>
      ) : shown.length === 0 ? (
        <Empty>{claims.isPending ? t("common.loading") : t("claimsPage.none")}</Empty>
      ) : (
        <div className="board">
          {columns.map((status) => {
            const cards = shown.filter((claim) => claim.status === status);
            if (!cards.length && !["open", "proved"].includes(status)) return null;
            return (
              <section className="board-column" key={status} aria-label={statusText(t, status)}>
                <header>
                  <span
                    className="status-dot"
                    data-tone={
                      status === "proved"
                        ? "ok"
                        : status === "needs_recheck" || status === "refuted"
                          ? "bad"
                          : status === "open"
                            ? "none"
                            : "warn"
                    }
                  />
                  <span>{statusText(t, status)}</span>
                  <span>{cards.length}</span>
                </header>
                {cards.map((claim) => {
                  const record = counts.get(claim.id);
                  const progress = claimProgress.get(claim.id);
                  const summary = claimProgressText(t, progress, claim.status);
                  const live = running.get(claim.id) ?? 0;
                  const repeatColumn =
                    summary.label === statusText(t, status) ||
                    (status === "awaiting_interpretation_review" && summary.state === "decision");
                  const reviewItems = claimReviews.get(claim.id);
                  const showRunning = live > 0;
                  const showReview = Boolean(reviewItems?.length);
                  const repeatCount =
                    (showReview && summary.state === "review") || (showRunning && summary.state === "running");
                  const showBlocked =
                    Boolean(record?.blocked) && summary.state !== "decision" && summary.state !== "recheck";
                  const expanded = expandedClaim === claim.id;
                  const previewId = "claim-preview-" + claim.id;
                  return (
                    <article className="board-card" key={claim.id} data-live={String(live > 0)}>
                      <button
                        type="button"
                        className="board-card-toggle"
                        aria-expanded={expanded}
                        aria-controls={expanded ? previewId : undefined}
                        onClick={() => setExpandedClaim(expanded ? null : claim.id)}
                      >
                        <span className="title">{claim.title || t("sidebar.untitled")}</span>
                        {progress?.obligations && progress.obligations.total > 0 && (
                          <span className="progress" aria-hidden="true">
                            <span
                              className="done"
                              style={{
                                width: `${Math.round((progress.obligations.verified / progress.obligations.total) * 100)}%`,
                              }}
                            />
                          </span>
                        )}
                        <ClaimProgressSummary {...summary} showState={!repeatColumn && !repeatCount} />
                        <span className="meta">{claim.domain || t("sidebar.uncategorized")}</span>
                      </button>
                      {(showReview || showBlocked || showRunning) && (
                        <div className="tags board-card-tags">
                          {showRunning && (
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
                      )}
                      {expanded && (
                        <ClaimPreview
                          session={session}
                          claimId={claim.id}
                          id={previewId}
                          onOpen={() => onOpen(claim.id)}
                        />
                      )}
                    </article>
                  );
                })}
              </section>
            );
          })}
        </div>
      )}
    </SectionLayout>
  );
}
