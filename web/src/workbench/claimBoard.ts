// #326: the research board groups claims by domain and shows each claim's proof obligations as a row of segments.
import type { ClaimAssurance, ObligationCount } from "../api/projectCanvas";
import type { AtlasClaim } from "./types";

export type Segment = "done" | "live" | "open" | "failed";

/** Statuses an obligation node can reach that mean its last check failed. */
const FAILED = new Set(["failed", "rejected", "needs_recheck", "refuted"]);

/**
 * One segment per proof obligation. The atlas names each obligation's status; without it the verified and total
 * counts still give the shape. When work is running on the claim, the first unfinished obligation shows as live.
 */
export function obligationSegments(
  nodes: AtlasClaim["nodes"] | undefined,
  counts: ObligationCount | undefined,
  running: boolean,
): Segment[] {
  const obligations = (nodes ?? []).filter((node) => node.kind === "obligation");
  const segments: Segment[] = obligations.length
    ? obligations.map((node) =>
        node.status === "verified" ? "done" : FAILED.has(node.status ?? "") ? "failed" : "open",
      )
    : counts && counts.total > 0
      ? Array.from({ length: counts.total }, (_, index) => (index < counts.verified ? "done" : "open"))
      : [];
  if (running) {
    const next = segments.indexOf("open");
    if (next >= 0) segments[next] = "live";
  }
  return segments;
}

export function segmentSummary(segments: readonly Segment[]): { done: number; total: number; failed: number } {
  return {
    done: segments.filter((segment) => segment === "done").length,
    total: segments.length,
    failed: segments.filter((segment) => segment === "failed").length,
  };
}

/** Columns always shown first; any other status a project uses follows in the order it first appears. */
export const BOARD_STATUSES = ["open", "awaiting_interpretation_review", "needs_recheck", "proved", "refuted"] as const;

export function boardColumns(statuses: readonly string[]): string[] {
  const extra = [...new Set(statuses.filter((status) => !(BOARD_STATUSES as readonly string[]).includes(status)))];
  const known = BOARD_STATUSES.filter((status) => status !== "refuted" || statuses.includes("refuted"));
  return [...known, ...extra];
}

/** Columns the domain rows show: In progress and Proved always, other statuses only while a claim has them. */
export function laneColumns(columns: readonly string[], statuses: readonly string[]): string[] {
  const present = new Set(statuses);
  return columns.filter((status) => status === "open" || status === "proved" || present.has(status));
}

export type Lane<T> = { key: string; claims: T[] };

/** Claims grouped by domain, in name order, with the unnamed group last. */
export function laneGroups<T extends { domain: string | null }>(claims: readonly T[], locale: string): Lane<T>[] {
  const groups = new Map<string, T[]>();
  for (const claim of claims) {
    const key = claim.domain ?? "";
    groups.set(key, [...(groups.get(key) ?? []), claim]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => (a === "" ? 1 : b === "" ? -1 : a.localeCompare(b, locale)))
    .map(([key, claims]) => ({ key, claims }));
}

/** Search matches the claim code, title or domain, ignoring case and surrounding space. */
export function matchesSearch(claim: { title: string | null; domain: string | null }, code: string, query: string) {
  const needle = query.trim().toLocaleLowerCase();
  if (!needle) return true;
  return [code, claim.title ?? "", claim.domain ?? ""].some((text) => text.toLocaleLowerCase().includes(needle));
}

/** The cards a cell shows before "Show N more"; `limit` 0 shows every card. */
export function visibleCards<T>(cards: readonly T[], limit: number, expanded: boolean): { shown: T[]; hidden: number } {
  if (expanded || limit <= 0 || cards.length <= limit) return { shown: [...cards], hidden: 0 };
  return { shown: cards.slice(0, limit), hidden: cards.length - limit };
}

// #345: what waits for review is in Needs you, so there is no separate review view.
export const CLAIM_VIEWS = ["all", "running", "blocked", "advance"] as const;
export type ClaimView = (typeof CLAIM_VIEWS)[number];

export type ClaimViewContext = {
  blocked: ReadonlyMap<string, number>;
  running: ReadonlyMap<string, number>;
  progress: ReadonlyMap<string, { canAdvance?: boolean }>;
};

/** Whether a claim belongs to a Work view; the panel counts and the Claims page filter use the same rule. */
export function inClaimView(view: ClaimView, claimId: string, context: ClaimViewContext): boolean {
  if (view === "blocked") return (context.blocked.get(claimId) ?? 0) > 0;
  if (view === "running") return (context.running.get(claimId) ?? 0) > 0;
  // #342: open claims with nothing they rely on still unproved.
  if (view === "advance") return context.progress.get(claimId)?.canAdvance === true;
  return true;
}

export type TrustMark = "lean" | "certificate" | "audited" | "counterexample" | "numeric";

/** #342: recorded evidence kinds map to marks here, in the client, so no kind list enters kernel SQL. */
const DECISIVE_MARKS: Readonly<Record<string, TrustMark>> = {
  lean: "lean",
  exact_certificate: "certificate",
  audited_proof: "audited",
  counterexample: "counterexample",
};

/**
 * How a claim was established: the decisive evidence's kind and whether it was approved in Review. An open claim
 * whose only verified evidence includes a numeric check shows that support instead.
 */
export function trustMark(
  status: string,
  assurance: ClaimAssurance | undefined,
): { mark: TrustMark; reviewed: boolean } | null {
  const decisive = assurance?.kind ? DECISIVE_MARKS[assurance.kind] : undefined;
  if (decisive && status !== "open") return { mark: decisive, reviewed: assurance?.review === "approved" };
  if (status === "open" && assurance?.supporting.includes("numeric")) return { mark: "numeric", reviewed: false };
  return null;
}
