import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { activityNode, type Graph } from "../workbench/graph";
import { modelName, nodeTitle, statusText, summarize, timeText } from "../workbench/text";
import type { ActivityRecord, ActivitySnapshot } from "../workbench/types";
import { Chevron, Mark, Spin } from "./Icons";

type Draft = {
  text: string;
  audience?: string;
  confirmRelease?: boolean;
  requestId?: string;
  requestText?: string;
  sending?: boolean;
  message?: string;
  error?: boolean;
};
type Entry = ActivityRecord & { kind: string };

const AUDIENCES = ["local", "codex"] as const;

export function ChatPanel({
  session,
  graph,
  claimId,
  selected,
  activity,
  activityError,
  running,
  queued,
  open,
  allNodes,
  onToggle,
  onAllNodes,
  onSelect,
  onSent,
}: {
  session: string;
  graph: Graph | null;
  claimId: string | null;
  selected: string | null;
  activity: ActivitySnapshot | undefined;
  activityError: string;
  running: number;
  queued: number;
  open: boolean;
  allNodes: boolean;
  onToggle: () => void;
  onAllNodes: () => void;
  onSelect: (id: string) => void;
  onSent: () => void;
}) {
  const { t, i18n } = useTranslation();
  const feed = useRef<HTMLOListElement>(null);
  const nearBottom = useRef(true);
  const target = graph && selected ? graph.nodes.get(selected) : undefined;

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
    .filter((record) => allNodes || activityNode(graph, record) === selected)
    .sort((a, b) => live(a) - live(b) || time(a) - time(b));

  const onScroll = () => {
    const element = feed.current;
    if (element) nearBottom.current = element.scrollHeight - element.scrollTop - element.clientHeight < 40;
  };
  useLayoutEffect(() => {
    const element = feed.current;
    if (element && nearBottom.current) element.scrollTop = element.scrollHeight;
  }, [shown.length, selected, allNodes]);

  const relation = (id: string) => {
    const node = graph?.nodes.get(id);
    if (!node) return null;
    return (
      <button type="button" className="relation node-link" onClick={() => onSelect(id)}>
        {nodeTitle(t, node)}
      </button>
    );
  };

  const entry = (record: Entry) => {
    const at = record.at || record.created_at;
    const link = allNodes
      ? (() => {
          const node = activityNode(graph, record);
          return node ? relation(node) : null;
        })()
      : null;
    if (record.kind === "guidance") {
      const towardsCodex = record.audience === "codex";
      const progress = record.submitted_at
        ? t(towardsCodex ? "chat.submittedCodex" : "chat.submittedLocal")
        : t(towardsCodex ? "chat.waitingCodex" : "chat.waitingLocal");
      const audience = t(`audience.${record.audience ?? "local"}`);
      return (
        <li key={record.id} data-from="you">
          <p className="bubble">{record.text ?? ""}</p>
          <span className="speaker">
            {[t("chat.you"), timeText(i18n.language, at, true), t("chat.sentTo", { audience }), progress]
              .filter(Boolean)
              .join(" · ")}
          </span>
          {link}
        </li>
      );
    }
    if (record.state === "running" && ["model", "worker", "lean", "task"].includes(record.kind)) {
      const who = record.kind === "model" ? modelName(t, record) : t(`activityKind.${record.kind}`);
      return (
        <li key={record.id}>
          <span className="live">
            <Spin />
            <b>{who}</b>
            {record.kind === "worker" ? t("chat.workerRunning") : t("chat.taskRunning")}
          </span>
          {link}
        </li>
      );
    }
    if (record.kind === "agent_feedback" || record.kind === "verifier_feedback") {
      return (
        <li key={record.id}>
          <span className="speaker">
            <b>{record.kind === "agent_feedback" ? modelName(t, record) : t("chat.lean")}</b>
            {timeText(i18n.language, at, true)}
          </span>
          <p className="message">{record.text ?? ""}</p>
          {link}
        </li>
      );
    }
    const label =
      record.kind === "model"
        ? `${t("activityKind.model")} · ${modelName(t, record)}`
        : t(`activityKind.${record.kind}`, { defaultValue: t("chat.record") });
    const tone = ["failed", "blocked", "lease_expired"].includes(record.state)
      ? "bad"
      : ["queued", "waiting_retry", "waiting_dependency"].includes(record.state)
        ? "planned"
        : "ok";
    return (
      <li key={record.id}>
        <span className="tool" title={record.text ?? ""}>
          <Chevron />
          {label}
          <span className="result">
            <Mark tone={tone} />
            {t(`activityState.${record.state}`, { defaultValue: statusText(t, record.state) })}
          </span>
          {timeText(i18n.language, at, true)}
        </span>
        {link}
      </li>
    );
  };

  return (
    <section className="chat" aria-labelledby="activity-title" data-collapsed={String(!open)}>
      <header className="chat-head">
        <h2 id="activity-title" tabIndex={-1}>
          {t("chat.title")}
          <span id="chat-target">{allNodes ? t("chat.wholeClaim") : target ? nodeTitle(t, target) : ""}</span>
        </h2>
        <span className="chat-state" role="status" data-error={String(Boolean(activityError))}>
          {running > 0 && !activityError && <Spin />}
          {summary}
        </span>
        <button type="button" className="chip" aria-pressed={allNodes} onClick={onAllNodes}>
          {t("chat.allNodes")}
        </button>
        <button
          id="toggle-chat"
          type="button"
          className="icon-button"
          aria-expanded={open}
          aria-controls="chat-body"
          aria-label={open ? t("chat.collapse") : t("chat.expand")}
          onClick={onToggle}
        >
          <svg viewBox="0 0 12 12" aria-hidden="true">
            <path d="m2.5 4.5 3.5 3.5 3.5-3.5" />
          </svg>
        </button>
      </header>
      <div id="chat-body" className="chat-body" hidden={!open}>
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
          activity={activity}
          activityError={activityError}
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
  activity,
  activityError,
  onSent,
}: {
  session: string;
  graph: Graph | null;
  claimId: string | null;
  selected: string | null;
  activity: ActivitySnapshot | undefined;
  activityError: string;
  onSent: () => void;
}) {
  const { t } = useTranslation();
  const drafts = useRef(new Map<string, Draft>());
  const [, rerender] = useState(0);
  const refresh = () => rerender((value) => value + 1);
  const node = graph && selected ? graph.nodes.get(selected) : undefined;
  const statement = graph?.statementId ?? null;
  const key = claimId && node ? [claimId, statement, node.id].join("/") : null;
  const draft: Draft | null = key ? (drafts.current.get(key) ?? drafts.current.set(key, { text: "" }).get(key)!) : null;

  const declared = Array.isArray(activity?.guidance_channels);
  const channels: string[] = declared
    ? AUDIENCES.filter((channel) => activity!.guidance_channels!.includes(channel))
    : activity?.guidance_available
      ? ["local"]
      : [];
  const available = Boolean(statement && node && channels.length && !activityError);
  useEffect(() => {
    if (draft?.audience && declared && !channels.includes(draft.audience)) {
      draft.audience = undefined;
      draft.confirmRelease = false;
      draft.requestId = undefined;
      refresh();
    }
  });
  const audience = draft?.audience ?? (channels.includes("local") ? "local" : (channels[0] ?? ""));
  const towardsCodex = audience === "codex";
  const disabled = !available || Boolean(draft?.sending);
  const confirming = Boolean(towardsCodex && draft?.confirmRelease && !draft?.sending);
  const canSend = !disabled && Boolean(draft?.text.trim()) && channels.includes(audience);
  const viewOnly = !available && activity ? activity.guidance_reason || t("composer.viewOnly") : "";
  const status = confirming ? t("composer.confirmHint") : draft?.message || viewOnly;

  const send = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!draft || !node || !claimId || !statement || !canSend) return;
    if (towardsCodex && !draft.confirmRelease) {
      // Sending to an external service takes a second, explicit click.
      draft.confirmRelease = true;
      refresh();
      return;
    }
    const text = draft.text.trim();
    draft.requestText = text;
    draft.requestId ||= crypto.randomUUID();
    draft.sending = true;
    draft.message = "";
    draft.error = false;
    refresh();
    try {
      await api(session, "/api/guidance", {
        data: {
          claim_id: claimId,
          statement_id: statement,
          node_id: node.id,
          text,
          request_id: draft.requestId,
          ...(declared ? { audience } : {}),
          ...(towardsCodex ? { confirm_release: true } : {}),
        },
      });
      draft.text = "";
      draft.requestId = undefined;
      draft.audience = undefined;
      draft.confirmRelease = false;
      draft.message = t(towardsCodex ? "composer.sentCodex" : "composer.sentLocal");
      onSent();
    } catch (error) {
      draft.message =
        error instanceof Error ? t(`errors.${error.message}`, { defaultValue: error.message }) : String(error);
      draft.error = true;
      draft.confirmRelease = false;
    } finally {
      draft.sending = false;
      refresh();
    }
  };

  const name = node && graph ? summarize(nodeTitle(t, node), 14) + " · " : "";
  return (
    <form id="guidance-form" className="composer" onSubmit={send}>
      <label htmlFor="guidance-text" className="sr-only">
        {t("composer.label")}
      </label>
      <textarea
        id="guidance-text"
        rows={2}
        maxLength={1500}
        required
        placeholder={t("composer.placeholder")}
        aria-describedby="guidance-scope guidance-status"
        disabled={disabled}
        value={draft?.text ?? ""}
        onChange={(event) => {
          if (!draft) return;
          if (draft.text.trim() !== event.target.value.trim()) draft.confirmRelease = false;
          draft.text = event.target.value;
          if (draft.text.trim() !== draft.requestText) draft.requestId = undefined;
          draft.message = "";
          draft.error = false;
          refresh();
        }}
      />
      <div className="composer-row">
        <label htmlFor="guidance-audience" className="sr-only">
          {t("composer.audience")}
        </label>
        <select
          id="guidance-audience"
          aria-describedby="guidance-scope"
          disabled={disabled}
          value={audience}
          onChange={(event) => {
            if (!draft) return;
            draft.audience = event.target.value;
            draft.confirmRelease = false;
            draft.requestId = undefined;
            draft.message = "";
            draft.error = false;
            refresh();
          }}
        >
          {channels.length === 0 ? (
            <option value="">{t("composer.unavailable")}</option>
          ) : (
            channels.map((channel) => (
              <option key={channel} value={channel}>
                {name + t(`audience.${channel}`)}
              </option>
            ))
          )}
        </select>
        <p id="guidance-scope" className="composer-note">
          {!available ? "" : towardsCodex ? t("composer.scopeCodex") : t("composer.scopeLocal")}
        </p>
        <button
          id="guidance-send"
          type="submit"
          className="send"
          data-confirm={String(confirming)}
          disabled={!canSend}
          aria-label={
            draft?.sending ? t("composer.sending") : confirming ? t("composer.confirmLabel") : t("composer.send")
          }
        >
          {confirming ? (
            t("composer.confirm")
          ) : (
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path d="M12 19V5" />
              <path d="m5 12 7-7 7 7" />
            </svg>
          )}
        </button>
      </div>
      <p
        id="guidance-status"
        className="composer-status"
        role="status"
        data-error={String(Boolean(draft?.error))}
        data-confirm={String(confirming)}
      >
        {status}
      </p>
    </form>
  );
}
