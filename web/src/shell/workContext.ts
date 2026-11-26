// #326: what the Work panel and the side panel share — items waiting for you, claim views, pins and the open item.
import { createContext, useContext, useState } from "react";
import type { TeamAgent } from "../api/teamAgents";
import type { ActivityRow } from "./workspaceActivity";

export type WaitingKind = "review" | "confirm" | "question" | "failed" | "delivery";
export type WaitingItem = { id: string; kind: WaitingKind; row: ActivityRow; claimId?: string | null };

const FAILED_JOBS = new Set(["failed", "lease_expired"]);
const WORK_NEEDS_YOU = new Set(["delivered", "blocked", "failed", "interrupted"]);

/** A record that needs the researcher: a review or question, a failed run, or an Agent delivery to decide. */
export function waitingKind(row: ActivityRow): WaitingKind | null {
  if (row.source === "review") {
    if (row.state === "review") return "review";
    // #340: a scope, statement or source to confirm is not a question to answer.
    return row.item.kind.startsWith("confirm_") ? "confirm" : "question";
  }
  if (row.source === "job") return FAILED_JOBS.has(row.job.state) ? "failed" : null;
  if (row.source === "work") {
    if (!WORK_NEEDS_YOU.has(row.task.status)) return null;
    return row.task.status === "delivered" ? "delivery" : "failed";
  }
  return null;
}

/** Waiting records, newest first, leaving out what the Inbox has archived. */
export function waitingItems(rows: readonly ActivityRow[], archived: ReadonlySet<string> = new Set()): WaitingItem[] {
  return rows
    .filter((row) => !archived.has(row.id))
    .map((row) => ({ row, kind: waitingKind(row) }))
    .filter((entry): entry is { row: ActivityRow; kind: WaitingKind } => entry.kind !== null)
    .sort((a, b) => (b.row.time?.at ?? "").localeCompare(a.row.time?.at ?? ""))
    .map(({ row, kind }) => ({ id: row.id, kind, row, claimId: row.claimId }));
}

/** The kinds present in the waiting list, in a fixed order, with how many of each. */
export const WAITING_KINDS: readonly WaitingKind[] = ["review", "confirm", "question", "failed", "delivery"];

export function waitingKindCounts(items: readonly WaitingItem[]): { kind: WaitingKind; count: number }[] {
  return WAITING_KINDS.map((kind) => ({ kind, count: items.filter((item) => item.kind === kind).length })).filter(
    ({ count }) => count > 0,
  );
}

export function shownWaiting(items: WaitingItem[], kind: WaitingKind | null): WaitingItem[] {
  return kind ? items.filter((item) => item.kind === kind) : items;
}

export type PeekTarget =
  | { kind: "waiting"; id: string; index: number }
  | { kind: "claim"; id: string; order: string[] };

/**
 * The item a side panel shows. A decided item leaves the waiting list, so the panel shows the one that took its
 * place: deciding an item moves on to the next without another step.
 */
export function resolveWaiting(items: readonly WaitingItem[], target: { id: string; index: number }) {
  const found = items.findIndex((item) => item.id === target.id);
  const index = found >= 0 ? found : Math.min(target.index, items.length - 1);
  return index >= 0 ? { item: items[index]!, index } : null;
}

export function stepTarget(target: PeekTarget, items: readonly WaitingItem[], step: 1 | -1): PeekTarget | null {
  if (target.kind === "waiting") {
    const current = resolveWaiting(items, target);
    if (!current) return null;
    const next = items[current.index + step];
    return next ? { kind: "waiting", id: next.id, index: current.index + step } : null;
  }
  const index = target.order.indexOf(target.id);
  const next = target.order[index + step];
  return next ? { ...target, id: next } : null;
}

const PIN_KEY = "trellin.work.pinned";

/** Claims pinned for quick return, kept on this device (#301 keeps them across devices later). */
export function usePinnedClaims() {
  const [pinned, setPinned] = useState<string[]>(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(PIN_KEY) ?? "[]");
      return Array.isArray(saved) ? saved.filter((id): id is string => typeof id === "string") : [];
    } catch {
      return [];
    }
  });
  function toggle(id: string) {
    const next = pinned.includes(id) ? pinned.filter((entry) => entry !== id) : [id, ...pinned];
    setPinned(next);
    try {
      window.localStorage.setItem(PIN_KEY, JSON.stringify(next));
    } catch {
      // Pinning still applies to this window.
    }
  }
  return [pinned, toggle] as const;
}

export type WorkView = { key: string; href: string; count: number };
export type WorkClaim = { id: string; title: string | null; code: string };

export type WorkContextValue = {
  agents: ReadonlyMap<string, TeamAgent>;
  /** #345: everything waiting for the researcher. Every count of waiting items reads this list. */
  waiting: WaitingItem[];
  /** The kind the Needs you list is narrowed to, or null for all. */
  waitingFilter: WaitingKind | null;
  setWaitingFilter: (kind: WaitingKind | null) => void;
  /** The waiting items the panel lists and the floating window steps through. */
  shown: WaitingItem[];
  views: WorkView[];
  pinned: WorkClaim[];
  isPinned: (claimId: string) => boolean;
  togglePin: (claimId: string) => void;
  peek: PeekTarget | null;
  setPeek: (target: PeekTarget | null) => void;
  /** The side panel's content, built by the shell, which owns the data it needs. */
  renderPeek: () => React.ReactNode;
};

export const WorkContext = createContext<WorkContextValue | null>(null);

export function useWork() {
  return useContext(WorkContext);
}
