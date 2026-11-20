import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../api/client";
import { activityNode, type Graph } from "../workbench/graph";
import { isCodex, modelName, nodeTitle } from "../workbench/text";
import type { ActivityRecord, ActivitySnapshot } from "../workbench/types";
import { FeedEntry } from "./FeedEntry";
import { SendArrow, Spin } from "./Icons";
import { WorkbenchConversations } from "./WorkbenchConversations";

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
  scopeReady,
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
  scopeReady: boolean;
  running: number;
  queued: number;
  open: boolean;
  allNodes: boolean;
  onToggle: () => void;
  onAllNodes: () => void;
  onSelect: (id: string) => void;
  onSent: () => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"chat" | "proof">("chat");
  const feed = useRef<HTMLOListElement>(null);
  const nearBottom = useRef(true);
  const target = graph && selected ? graph.nodes.get(selected) : undefined;
  const anchor = {
    ...(claimId ? { claim_id: claimId } : {}),
    ...(graph?.statementId ? { statement_id: graph.statementId } : {}),
    ...(!allNodes && target ? { node_id: target.id } : {}),
  };
  const anchorKey = [session, anchor.claim_id, anchor.statement_id, anchor.node_id].join("/");
  const conversationTitle =
    !allNodes && target ? nodeTitle(t, target) : graph?.claim.title || t("conversations.defaultTitle");

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
  }, [shown.length, selected, allNodes, tab]);

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
    const node = allNodes ? activityNode(graph, record) : null;
    return <FeedEntry key={record.id} record={record} link={node ? relation(node) : null} />;
  };

  return (
    <section className="chat" aria-labelledby="activity-title" data-collapsed={String(!open)}>
      <header className="chat-head">
        <h2 id="activity-title" tabIndex={-1}>
          {t("chat.title")}
          {!allNodes && target && graph?.codes.get(target.id) && (
            <span className="chat-code">{graph.codes.get(target.id)}</span>
          )}
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
        <div className="conversation-tabs" role="group" aria-label={t("conversations.views")}>
          <button type="button" className="chip" aria-pressed={tab === "chat"} onClick={() => setTab("chat")}>
            {t("rail.chat")}
          </button>
          <button type="button" className="chip" aria-pressed={tab === "proof"} onClick={() => setTab("proof")}>
            {t("conversations.proofActivity")}
          </button>
        </div>
        {tab === "chat" && scopeReady && (
          <WorkbenchConversations key={anchorKey} session={session} anchor={anchor} title={conversationTitle} />
        )}
        <div className="chat-body" hidden={tab !== "proof"}>
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
  const key = claimId && node ? JSON.stringify([session, claimId, statement, node.id]) : null;
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
  const blocker = !node
    ? t("composer.chooseNode")
    : activityError ||
      (!activity
        ? t("chat.loading")
        : activity.guidance_blocker
          ? t(`composer.blockers.${activity.guidance_blocker}`, {
              defaultValue: activity.guidance_reason || t("composer.viewOnly"),
            })
          : activity.guidance_reason || t("composer.viewOnly"));
  const status = !available ? blocker : confirming ? t("composer.confirmHint") : draft?.message || "";
  const draftOnly = Boolean(draft && !available);

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
      <label htmlFor="guidance-text" className="sr-only">
        {t("composer.label")}
      </label>
      <textarea
        id="guidance-text"
        rows={2}
        maxLength={1500}
        required
        placeholder={t(draftOnly ? "composer.draftPlaceholder" : "composer.placeholder")}
        aria-describedby={draftOnly ? "guidance-status guidance-draft" : "guidance-status"}
        disabled={!draft || Boolean(draft.sending)}
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
          disabled={disabled}
          value={available ? audience : ""}
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
          {!available ? (
            <option value="">{t("composer.unavailable")}</option>
          ) : (
            channels.map((channel) => (
              <option key={channel} value={channel}>
                {recipient(channel)}
              </option>
            ))
          )}
        </select>
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
          {confirming ? t("composer.confirm") : <SendArrow />}
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
      {draftOnly && (
        <p id="guidance-draft" className="composer-status">
          {t("composer.draftOnly")}
        </p>
      )}
    </form>
  );
}
