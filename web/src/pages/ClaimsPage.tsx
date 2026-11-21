import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { useClaims, type Job } from "../api/sections";
import { ClaimProgressSummary, claimProgressText } from "../components/ClaimProgress";
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
  atlasClaims,
  jobs,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string) => void;
  claimProgress: ReadonlyMap<string, ClaimProgress>;
  atlasClaims?: AtlasClaim[];
  jobs?: Job[];
}) {
  const { t } = useTranslation();
  const [expandedClaim, setExpandedClaim] = useState<string | null>(null);
  const claims = useClaims(session);
  const counts = new Map((atlasClaims ?? []).map((claim) => [claim.id, claim]));
  const running = new Map<string, number>();
  for (const job of jobs ?? []) {
    if (job.state === "running" && job.claim_id) running.set(job.claim_id, (running.get(job.claim_id) ?? 0) + 1);
  }
  const list = claims.data?.claims ?? [];
  const domains = [...new Set(list.map((claim) => claim.domain || t("sidebar.uncategorized")))].sort();
  const [filter, domain] = sub.startsWith("domain:")
    ? ["all", sub.slice(7)]
    : [FILTERS.includes(sub as never) ? sub : "all", ""];
  const shown = list.filter((claim) => {
    const record = counts.get(claim.id);
    if (domain && (claim.domain || t("sidebar.uncategorized")) !== domain) return false;
    if (filter === "review") return (record?.awaiting_review ?? 0) > 0;
    if (filter === "blocked") return (record?.blocked ?? 0) > 0;
    if (filter === "running") return (running.get(claim.id) ?? 0) > 0;
    return true;
  });
  const filterCount = (key: (typeof FILTERS)[number]) =>
    list.filter((claim) =>
      key === "all"
        ? true
        : key === "review"
          ? (counts.get(claim.id)?.awaiting_review ?? 0) > 0
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
                  const live = running.get(claim.id) ?? 0;
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
                        {progress?.nodes && progress.nodes.total > 0 && (
                          <span className="progress" aria-hidden="true">
                            <span
                              className="done"
                              style={{ width: `${Math.round((progress.nodes.done / progress.nodes.total) * 100)}%` }}
                            />
                          </span>
                        )}
                        <ClaimProgressSummary {...claimProgressText(t, progress, claim.status)} />
                        <span className="meta">{claim.domain || t("sidebar.uncategorized")}</span>
                        {(record?.awaiting_review || record?.blocked || live > 0) && (
                          <span className="tags">
                            {live > 0 && (
                              <span className="tag" data-tone="live">
                                <Spin />
                                {t("claimsPage.running", { count: live })}
                              </span>
                            )}
                            {(record?.awaiting_review ?? 0) > 0 && (
                              <span className="tag" data-tone="review">
                                {t("sidebar.awaitingReview", { count: record!.awaiting_review })}
                              </span>
                            )}
                            {(record?.blocked ?? 0) > 0 && (
                              <span className="tag" data-tone="bad">
                                {t("sidebar.blocked", { count: record!.blocked })}
                              </span>
                            )}
                          </span>
                        )}
                      </button>
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
