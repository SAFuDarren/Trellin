import { useTranslation } from "react-i18next";
import { useAtlas, useClaims, useTeam } from "../api/sections";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { Spin } from "../components/Icons";
import { href } from "../shell/route";
import { statusText } from "../workbench/text";

const COLUMNS = ["open", "awaiting_interpretation_review", "needs_recheck", "proved", "refuted"] as const;
const DONE = new Set(["verified", "proved", "proved_goal", "approved", "route_checked", "lemma_verified"]);
const FILTERS = ["all", "review", "blocked", "running"] as const;

export function ClaimsPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string) => void;
}) {
  const { t } = useTranslation();
  const claims = useClaims(session);
  const atlas = useAtlas(session);
  const team = useTeam(session);
  const counts = new Map((atlas.data?.claims ?? []).map((claim) => [claim.id, claim]));
  const running = new Map<string, number>();
  for (const job of team.data?.jobs ?? []) {
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
                  const total = record?.nodes.length ?? 0;
                  const done = record?.nodes.filter((node) => DONE.has(node.status ?? "")).length ?? 0;
                  const live = running.get(claim.id) ?? 0;
                  return (
                    <button
                      type="button"
                      className="board-card"
                      key={claim.id}
                      data-live={String(live > 0)}
                      onClick={() => onOpen(claim.id)}
                    >
                      <span className="title">{claim.title || t("sidebar.untitled")}</span>
                      {total > 0 && (
                        <span className="progress" aria-hidden="true">
                          <span className="done" style={{ width: `${Math.round((done / total) * 100)}%` }} />
                        </span>
                      )}
                      <span className="meta">
                        <span>{claim.domain || t("sidebar.uncategorized")}</span>
                        {total > 0 && <span>{"· " + t("claimsPage.verified", { done, total })}</span>}
                      </span>
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
