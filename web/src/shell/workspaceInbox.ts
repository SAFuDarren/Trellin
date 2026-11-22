import { useState } from "react";
import { workspaceId } from "../api/workspaceEvents";
import type { ActivityRow } from "./workspaceActivity";

export type InboxRecord = Exclude<ActivityRow, { source: "chat" }>;
export type TriageMark = "unread" | "read" | "archived";
type Mark = { state: Exclude<TriageMark, "unread">; revision: string };
type Triage = {
  scope: string;
  marks: ReadonlyMap<string, Mark>;
  undo?: { id: string; previous?: Mark };
};

const FAILED_JOBS = new Set(["failed", "lease_expired"]);
const INBOX_WORK = new Set(["delivered", "accepted", "blocked", "failed", "interrupted"]);

function inboxRecord(row: ActivityRow): row is InboxRecord {
  return (
    row.source === "review" ||
    (row.source === "job" && FAILED_JOBS.has(row.job.state)) ||
    (row.source === "work" && INBOX_WORK.has(row.task.status))
  );
}

function revision(record: InboxRecord): string {
  const state =
    record.source === "job" ? record.job.state : record.source === "work" ? record.task.status : record.state;
  return `${state}:${record.time?.at ?? ""}`;
}

export function workspaceInboxScope(session: string, eventId: unknown, reviewId: unknown): string | null {
  const events = workspaceId(eventId) ? eventId : null;
  const review = workspaceId(reviewId) ? reviewId : null;
  if (events && review && events !== review) return null;
  const id = events ?? review;
  return id ? `${session}/${id}` : null;
}

export function useWorkspaceInbox(scope: string | null, activity: ActivityRow[]) {
  const [triage, setTriage] = useState<Triage | null>(null);
  const current = triage?.scope === scope ? triage : null;
  const rows = scope
    ? activity.filter(inboxRecord).map((record) => {
        const mark = current?.marks.get(record.id);
        return { record, mark: mark?.revision === revision(record) ? mark.state : ("unread" as const) };
      })
    : [];
  const unread = rows.filter(({ mark }) => mark === "unread").length;

  function mark(record: InboxRecord, state: TriageMark) {
    if (!scope) return;
    setTriage((previous) => {
      const marks = new Map(previous?.scope === scope ? previous.marks : undefined);
      const old = marks.get(record.id);
      const fingerprint = revision(record);
      const currentMark = old?.revision === fingerprint ? old.state : "unread";
      if (currentMark === state) return previous;
      if (state === "unread") marks.delete(record.id);
      else marks.set(record.id, { state, revision: fingerprint });
      return { scope, marks, undo: { id: record.id, previous: old } };
    });
  }

  function undo() {
    setTriage((previous) => {
      if (!previous || previous.scope !== scope || !previous.undo) return previous;
      const marks = new Map(previous.marks);
      const { id, previous: old } = previous.undo;
      if (old) marks.set(id, old);
      else marks.delete(id);
      return { scope: previous.scope, marks };
    });
  }

  return { enabled: Boolean(scope), rows, unread, undoId: current?.undo?.id, mark, undo };
}

export type WorkspaceInboxState = ReturnType<typeof useWorkspaceInbox>;
