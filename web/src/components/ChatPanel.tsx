import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import { useComposerKeyboard } from "../hooks/useComposerKeyboard";
import { desktopProjectUpgrade } from "../workbench/desktop";
import { activityNode, type Graph } from "../workbench/graph";
import { isCodex, modelName, nodeTitle } from "../workbench/text";
import type { ActivityRecord, ActivitySnapshot, ProofDraft } from "../workbench/types";
import { FeedEntry } from "./FeedEntry";
import { Chevron, Spin } from "./Icons";

type DraftOperation = "loading" | "saving" | "discarding";
type DraftError = "loadFailed" | "saveFailed" | "discardFailed" | "conflict";

type Draft = {
  saved?: ProofDraft | null;
  draftOperation?: DraftOperation;
  draftError?: DraftError;
  discarded?: boolean;
  text: string;
  audience?: string;
  requestId?: string;
  requestStatement?: string;
  requestText?: string;
  sending?: boolean;
  message?: string;
  error?: boolean;
};
type Entry = ActivityRecord & { kind: string };

const AUDIENCES = ["local", "codex"] as const;
const DRAFT_PATH = "/api/proof-drafts";
type DraftResponse = { draft: ProofDraft | null };

function isDraftConflict(error: unknown) {
  return error instanceof ApiError && error.status === 409;
}

function guidanceBlockerText(t: TFunction, activity: ActivitySnapshot, fallback: string) {
  const reason = activity.guidance_reason || fallback;
  return activity.guidance_blocker
    ? t(`composer.blockers.${activity.guidance_blocker}`, { defaultValue: reason })
    : reason;
}

export function ProofActivity({
  session,
  graph,
  claimId,
  selected,
  activity,
  activityError,
  running,
  queued,
  active,
  allNodes,
  onAllNodes,
  onSelect,
  onSent,
}: {
  session: string;
  graph: Graph;
  claimId: string;
  selected: string | null;
  activity: ActivitySnapshot | undefined;
  activityError: string;
  running: number;
  queued: number;
  active: boolean;
  allNodes: boolean;
  onAllNodes: () => void;
  onSelect: (id: string) => void;
  onSent: () => void;
}) {
  const { t } = useTranslation();
  const feed = useRef<HTMLOListElement>(null);
  const nearBottom = useRef(true);
  const target = selected ? graph.nodes.get(selected) : undefined;

  const summary = activityError
    ? activityError
    : !activity
      ? t("chat.loading")
      : running
        ? t("chat.running", { count: running })
        : queued
          ? t("chat.queued", { count: queued })
          : t("chat.idle");
  const rows: Entry[] = activity?.activity ?? [];
  const guidance: Entry[] = (activity?.guidance ?? []).map((note) => ({
    ...note,
    kind: "guidance",
    state: "recorded",
  }));
  const time = (entry: Entry) => Date.parse(entry.at || entry.created_at || "") || 0;
  const live = (entry: Entry) => (entry.state === "running" ? 1 : 0);
  const shown = [...rows, ...guidance]
    .filter((record) => allNodes || (selected && activityNode(graph, record) === selected))
    .sort((a, b) => live(a) - live(b) || time(a) - time(b));

  const latestGuidance = guidance[0]?.id;
  const onScroll = () => {
    const element = feed.current;
    if (element) nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
  };
  useLayoutEffect(() => {
    const element = feed.current;
    if (element && nearBottom.current) element.scrollTop = element.scrollHeight;
  }, [shown.length, latestGuidance, selected, allNodes, active]);

  const relation = (id: string) => {
    const node = graph?.nodes.get(id);
    if (!node) return null;
    return (
      <button type="button" className="relation node-link" onClick={() => onSelect(id)}>
        {nodeTitle(t, node)}
      </button>
    );
  };

  const guidanceContext =
    activity && !activityError
      ? {
          channels: Array.isArray(activity.guidance_channels) ? activity.guidance_channels : undefined,
          blocker: guidanceBlockerText(t, activity, t("chat.guidanceUnavailable")),
        }
      : undefined;
  const entry = (record: Entry) => {
    const node = allNodes ? activityNode(graph, record) : null;
    return (
      <FeedEntry
        key={record.id}
        record={record}
        link={node ? relation(node) : null}
        guidanceContext={guidanceContext}
      />
    );
  };

  return (
    <section className="chat" aria-label={t("conversations.proofActivity")}>
      <header className="chat-head">
        <h2>
          {!allNodes && target && graph.codes.get(target.id) && (
            <span className="chat-code">{graph.codes.get(target.id)}</span>
          )}
          {allNodes ? t("chat.wholeClaim") : target ? nodeTitle(t, target) : t("chat.wholeClaim")}
        </h2>
        <span className="chat-state" role="status" data-error={String(Boolean(activityError))}>
          {running > 0 && !activityError && <Spin />}
          {summary}
        </span>
        <button type="button" className="chip" aria-pressed={allNodes} onClick={onAllNodes}>
          {t("chat.allNodes")}
        </button>
      </header>
      <div className="chat-body">
        <ol ref={feed} className="chat-feed" aria-label={t("chat.feed")} onScroll={onScroll}>
          {shown.length ? (
            shown.map(entry)
          ) : (
            <li className="chat-empty">
              {!activity ? t("chat.waiting") : allNodes ? t("chat.emptyClaim") : t("chat.emptyNode")}
            </li>
          )}
        </ol>
        <Composer
          session={session}
          graph={graph}
          claimId={claimId}
          selected={selected}
          active={active}
          allNodes={allNodes}
          activity={activity}
          activityError={activityError}
          onQueue={() => {
            nearBottom.current = true;
          }}
          onSent={onSent}
        />
      </div>
    </section>
  );
}

function Composer({
  session,
  graph,
  claimId,
  selected,
  active,
  allNodes,
  activity,
  activityError,
  onQueue,
  onSent,
}: {
  session: string;
  graph: Graph | null;
  claimId: string | null;
  selected: string | null;
  active: boolean;
  allNodes: boolean;
  activity: ActivitySnapshot | undefined;
  activityError: string;
  onQueue: () => void;
  onSent: () => void;
}) {
  const { t } = useTranslation();
  const composerKeyboard = useComposerKeyboard();
  const drafts = useRef(new Map<string, Draft>());
  const [, rerender] = useState(0);
  const refresh = useCallback(() => rerender((value) => value + 1), []);
  const candidate = claimId && graph?.claim.id === claimId && selected ? graph.nodes.get(selected) : undefined;
  const node = candidate?.kind === "proposed_premise" ? undefined : candidate;
  const nodeId = node?.id;
  const statement = graph?.statementId ?? null;
  const key = claimId && nodeId ? JSON.stringify([session, claimId, nodeId]) : null;
  const draft: Draft | null = key ? (drafts.current.get(key) ?? drafts.current.set(key, { text: "" }).get(key)!) : null;

  const currentActivity = activity?.claim_id === claimId ? activity : undefined;
  const declared = Array.isArray(currentActivity?.guidance_channels);
  const channels: string[] = declared
    ? AUDIENCES.filter((channel) => currentActivity!.guidance_channels!.includes(channel))
    : currentActivity?.guidance_available
      ? ["local"]
      : [];
  const available = Boolean(statement && node && channels.length && !activityError);
  const draftAvailable = currentActivity?.proof_draft_available === true;
  useEffect(() => {
    if (draft?.audience && declared && !channels.includes(draft.audience)) {
      draft.audience = undefined;
      draft.requestId = undefined;
      refresh();
    }
  });
  const audience = draft?.audience ?? (channels.includes("local") ? "local" : (channels[0] ?? ""));
  const towardsCodex = audience === "codex";
  const busy = Boolean(draft?.sending || draft?.draftOperation);
  const canSend = available && !busy && Boolean(draft?.text.trim()) && channels.includes(audience);
  const blocker = !node
    ? t("composer.chooseNode")
    : activityError ||
      (currentActivity ? guidanceBlockerText(t, currentActivity, t("composer.viewOnly")) : t("chat.loading"));
  const queueHint = available ? t(towardsCodex ? "composer.confirmHint" : "composer.localQueueHint") : "";
  const status = draft?.sending ? t("composer.sending") : !available ? blocker : draft?.message || "";
  const draftOnly = Boolean(draft && !available);
  const needsUpgrade =
    currentActivity?.proof_draft_available === false ||
    currentActivity?.guidance_blocker === "connected_schema" ||
    currentActivity?.guidance_blocker === "codex_schema";
  const upgrade = draft && needsUpgrade && !activityError ? desktopProjectUpgrade() : undefined;
  const matchesSaved = Boolean(draft?.saved && draft.text === draft.saved.text);
  const canWriteDraft = draftAvailable && draft?.saved !== undefined && !busy && draft.draftError !== "conflict";
  let draftStatus = "";
  if (draft?.draftOperation) {
    draftStatus = t(`composer.draft.${draft.draftOperation}`);
  } else if (draft?.draftError) {
    draftStatus = t(`composer.draft.${draft.draftError}`);
  } else if (draft?.saved) {
    draftStatus = t(matchesSaved ? "composer.draft.saved" : "composer.draft.edited");
  } else if (draft?.discarded) {
    draftStatus = t(draft.text.trim() ? "composer.draft.discardedEdits" : "composer.draft.discarded");
  } else if (draftAvailable && draft?.text.trim()) {
    draftStatus = t("composer.draft.unsaved");
  } else if (draft && !draftAvailable && (currentActivity || activityError) && (draftOnly || draft.text.trim())) {
    draftStatus = t("composer.draftOnly");
  }
  const canReloadDraft = draft?.draftError === "loadFailed" || draft?.draftError === "conflict";

  const loadDraft = useCallback(
    async (replaceText: boolean, signal?: AbortSignal) => {
      if (!draft || !claimId || !nodeId) return;
      draft.draftOperation = "loading";
      draft.draftError = undefined;
      refresh();
      try {
        const params = new URLSearchParams({ claim_id: claimId, node_id: nodeId });
        const { draft: saved } = await api<DraftResponse>(session, DRAFT_PATH + "?" + params, { signal });
        if (signal?.aborted) return;
        if (replaceText || draft.text === (draft.saved?.text ?? "")) {
          draft.text = saved?.text ?? "";
          draft.requestId = undefined;
        }
        draft.saved = saved;
      } catch {
        if (!signal?.aborted) draft.draftError = "loadFailed";
      } finally {
        if (!signal?.aborted) {
          draft.draftOperation = undefined;
          refresh();
        }
      }
    },
    [claimId, draft, nodeId, refresh, session],
  );

  const needsDraftLoad = active && draftAvailable && draft?.saved === undefined && !draft?.draftError;
  useEffect(() => {
    if (!needsDraftLoad || !draft || draft.draftOperation) return;
    const request = new AbortController();
    void loadDraft(false, request.signal);
    return () => {
      request.abort();
      if (draft.draftOperation === "loading") draft.draftOperation = undefined;
    };
  }, [draft, loadDraft, needsDraftLoad]);

  const writeDraft = async (operation: "saving" | "discarding") => {
    if (!draft || !claimId || !nodeId || !canWriteDraft || draft.draftOperation || draft.sending) return;
    const discard = operation === "discarding";
    if (discard ? !draft.saved : !draft.text.trim() || matchesSaved) return;
    draft.draftOperation = operation;
    draft.draftError = undefined;
    draft.discarded = undefined;
    refresh();
    try {
      const { draft: saved } = await api<DraftResponse>(session, discard ? DRAFT_PATH + "/discard" : DRAFT_PATH, {
        data: {
          claim_id: claimId,
          node_id: nodeId,
          expected_revision: draft.saved?.revision ?? null,
          ...(!discard ? { text: draft.text } : {}),
        },
      });
      if (discard && matchesSaved) {
        draft.text = "";
        draft.requestId = undefined;
      }
      draft.saved = saved;
      draft.discarded = discard;
    } catch (error) {
      draft.draftError = isDraftConflict(error) ? "conflict" : discard ? "discardFailed" : "saveFailed";
    } finally {
      draft.draftOperation = undefined;
      refresh();
    }
  };

  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    if (
      !draft ||
      !nodeId ||
      !claimId ||
      !statement ||
      !canSend ||
      !draft.text.trim() ||
      draft.draftOperation ||
      draft.sending
    )
      return;
    const text = draft.text;
    const draftRevision = matchesSaved ? draft.saved?.revision : undefined;
    if (draft.requestStatement !== statement || draft.requestText !== text) draft.requestId = undefined;
    draft.requestStatement = statement;
    draft.requestText = text;
    draft.requestId ||= crypto.randomUUID();
    draft.sending = true;
    draft.message = "";
    draft.error = false;
    onQueue();
    refresh();
    try {
      const reply = await api<{ draft_consumed?: boolean; submitted_at?: string | null }>(session, "/api/guidance", {
        data: {
          claim_id: claimId,
          statement_id: statement,
          node_id: nodeId,
          text: draftRevision ? text : text.trim(),
          request_id: draft.requestId,
          ...(declared ? { audience } : {}),
          ...(towardsCodex ? { confirm_release: true } : {}),
          ...(draftRevision ? { draft_revision: draftRevision } : {}),
        },
      });
      if (draftRevision) {
        // When another window changed the draft, reload it instead of showing a stale saved receipt.
        draft.saved = reply.draft_consumed ? null : undefined;
        draft.draftError = undefined;
      }
      draft.text = draft.saved?.text ?? "";
      draft.discarded = undefined;
      draft.requestId = undefined;
      draft.audience = undefined;
      draft.message = t(
        reply.submitted_at ? "composer.sentDelivered" : towardsCodex ? "composer.sentCodex" : "composer.sentLocal",
      );
      onSent();
    } catch (error) {
      const reason =
        error instanceof Error ? t(`errors.${error.message}`, { defaultValue: error.message }) : String(error);
      draft.message = t("composer.queueFailed", { reason });
      draft.error = true;
    } finally {
      draft.sending = false;
      refresh();
    }
  };

  // Each recipient reads as "FLT-09 · Codex · GPT-6.2 Sol xhigh": the node, the harness and the agent last seen on it.
  const code = node && graph ? graph.codes.get(node.id) : undefined;
  const agentFor = (channel: string) => {
    const records = (activity?.activity ?? []).filter(
      (record) => record.node_id === node?.id && record.model && (channel === "codex") === isCodex(record),
    );
    const latest = records.sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""))[0];
    return latest ? modelName(t, latest) : "";
  };
  const recipient = (channel: string) =>
    [code, t(`audience.${channel}`), agentFor(channel)].filter(Boolean).join(" · ");
  return (
    <form id="guidance-form" className="composer" onSubmit={send}>
      {allNodes && node && (
        <p id="guidance-target" className="composer-status">
          {t("composer.draft.target", { node: [code, nodeTitle(t, node)].filter(Boolean).join(" · ") })}
        </p>
      )}
      <label htmlFor="guidance-text" className="sr-only">
        {t("composer.label")}
      </label>
      <textarea
        {...composerKeyboard}
        id="guidance-text"
        rows={2}
        maxLength={1500}
        required
        placeholder={t(draftOnly ? "composer.draftPlaceholder" : "composer.placeholder")}
        aria-describedby={[
          allNodes && node && "guidance-target",
          queueHint && "guidance-queue-hint",
          "guidance-status",
          draftStatus && "guidance-draft",
        ]
          .filter(Boolean)
          .join(" ")}
        disabled={!draft || busy}
        value={draft?.text ?? ""}
        onChange={(event) => {
          if (!draft) return;
          draft.text = event.target.value;
          draft.discarded = undefined;
          if (draft.text !== draft.requestText) draft.requestId = undefined;
          if (draft.draftError === "saveFailed" || draft.draftError === "discardFailed") draft.draftError = undefined;
          draft.message = "";
          draft.error = false;
          refresh();
        }}
      />
      {available && (
        <div className="composer-row">
          <div className="proof-recipient">
            <label htmlFor="guidance-audience" className="sr-only">
              {t("composer.audience")}
            </label>
            <select
              id="guidance-audience"
              disabled={busy}
              value={audience}
              onChange={(event) => {
                if (!draft) return;
                draft.audience = event.target.value;
                draft.requestId = undefined;
                draft.message = "";
                draft.error = false;
                refresh();
              }}
            >
              {channels.map((channel) => (
                <option key={channel} value={channel}>
                  {recipient(channel)}
                </option>
              ))}
            </select>
            <Chevron />
          </div>
          <button
            id="guidance-send"
            type="submit"
            className="button proof-queue-button"
            aria-describedby="guidance-queue-hint"
            disabled={!canSend}
          >
            {t(draft?.sending ? "composer.sending" : draft?.error ? "composer.retry" : "composer.send")}
          </button>
        </div>
      )}
      {queueHint && (
        <p id="guidance-queue-hint" className="composer-status">
          {queueHint}
        </p>
      )}
      <p
        id="guidance-status"
        className="composer-status"
        role={draft?.error ? "alert" : "status"}
        data-error={String(Boolean(draft?.error))}
      >
        {status}
      </p>
      {upgrade && (
        <div>
          <button type="button" className="button" onClick={upgrade}>
            {t("composer.upgrade")}
          </button>
        </div>
      )}
      {draft && draftAvailable && (
        <div className="composer-row proof-draft-actions">
          <button
            type="button"
            className="button"
            disabled={!canWriteDraft || !draft.text.trim() || matchesSaved}
            onClick={() => void writeDraft("saving")}
          >
            {t("composer.draft.save")}
          </button>
          {draft.saved && (
            <button
              type="button"
              className="button"
              disabled={!canWriteDraft}
              onClick={() => void writeDraft("discarding")}
            >
              {t("composer.draft.discard")}
            </button>
          )}
          {canReloadDraft && (
            <button
              type="button"
              className="button"
              disabled={busy}
              onClick={() => void loadDraft(draft.draftError === "conflict")}
            >
              {t(draft.draftError === "conflict" ? "composer.draft.reload" : "composer.draft.retry")}
            </button>
          )}
        </div>
      )}
      <p
        id="guidance-draft"
        className={draftStatus ? "composer-status" : "sr-only"}
        role="status"
        data-error={String(Boolean(draft?.draftError))}
      >
        {draftStatus}
      </p>
    </form>
  );
}
