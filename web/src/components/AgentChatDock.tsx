import { useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAgentWork } from "../api/agentWork";
import { ApiError } from "../api/client";
import {
  conversationErrorText,
  useConversationActions,
  useConversations,
  type ConversationAnchor,
  type ConversationSummary,
} from "../api/conversations";
import { useTeamAgents, type TeamAgent } from "../api/teamAgents";
import { useConversationSending } from "../shell/ConversationEditors";
import { href, type Route } from "../shell/route";
import { timeText } from "../workbench/text";
import { ConversationPanel } from "./ConversationPanel";
import { Chevron, Mark, Spin } from "./Icons";
import { Picker } from "./FloatingControls";
import { TeamAgentAvatar } from "./TeamAgentAvatar";
import "../styles/agent-chat-dock.css";

export type AgentChatAnchor = { anchor: ConversationAnchor; title: string };

type AgentDockView = "chat" | "activity";

export function AgentChatDock({
  session,
  route,
  anchorRequest,
}: {
  session: string;
  route: Route;
  anchorRequest: AgentChatAnchor | null;
}) {
  const { t, i18n } = useTranslation();
  const conversations = useConversations(session);
  const work = useAgentWork(session);
  const actions = useConversationActions(session);
  const sending = useConversationSending(session);
  const [view, setView] = useState<AgentDockView | null>(null);
  const [handledAnchor, setHandledAnchor] = useState<AgentChatAnchor | null>(null);
  const [anchor, setAnchor] = useState<AgentChatAnchor | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [agentId, setAgentId] = useState<string | null>(null);
  const [directModel, setDirectModel] = useState(false);
  const [title, setTitle] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [focusComposer, setFocusComposer] = useState(false);
  const [recovering, setRecovering] = useState(false);
  const latestAnchor = useRef(anchorRequest);
  const createRequest = useRef<{ key: string; id: string } | null>(null);
  const revision = useRef(0);
  const busy = useRef(false);
  const launcher = useRef<HTMLButtonElement>(null);
  const activityLauncher = useRef<HTMLButtonElement>(null);
  const closeControl = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const titleId = useId();

  const threads = conversations.data?.threads ?? [];
  const tasks = work.data?.tasks ?? [];
  const conversationStatus = (thread: ConversationSummary) =>
    thread.running
      ? t("conversations.working")
      : sending.has(thread.id)
        ? t("conversations.sending")
        : thread.latest_turn?.status === "failed"
          ? t("conversations.lastSendFailed")
          : thread.latest_turn?.status === "answered"
            ? t("conversations.replied")
            : "";
  const activeChats = threads.filter((thread) => thread.running || sending.has(thread.id));
  const failedChats = threads.filter(
    (thread) => !thread.running && !sending.has(thread.id) && thread.latest_turn?.status === "failed",
  );
  const queuedWork = tasks.filter((task) => task.status === "queued");
  const runningWork = tasks.filter((task) => task.status === "running");
  const activeWork = [...runningWork, ...queuedWork];
  const attentionWork = tasks.filter(
    ({ status }) => status === "delivered" || status === "failed" || status === "interrupted" || status === "blocked",
  );
  const profiles = useTeamAgents(session);
  const agents = profiles.data?.agents ?? [];
  const agentsById = new Map(agents.map((profile) => [profile.id, profile]));
  const thread = threads.find(({ id }) => id === chosenId);
  const agent = agentsById.get((thread ? thread.agent_id : agentId) ?? "");
  const fullChatId =
    route.section === "chat"
      ? route.sub || (!profiles.isPending && !agents.length ? threads[0]?.id : undefined)
      : undefined;
  const duplicate = Boolean(thread && thread.id === fullChatId);
  const runningCount = activeChats.length + runningWork.length;
  const attentionCount = failedChats.length + attentionWork.length;

  if (anchorRequest && anchorRequest !== handledAnchor) {
    setHandledAnchor(anchorRequest);
    setAnchor(anchorRequest);
    setChosenId(null);
    setTitle(anchorRequest.title.slice(0, 200));
    setError("");
    setRecovering(false);
    setFocusComposer(false);
    setView("chat");
  }

  function open(kind: AgentDockView) {
    if (view === kind) {
      setView(null);
      return;
    }
    setView(kind);
  }

  useLayoutEffect(() => {
    if (view !== null) closeControl.current?.focus();
  }, [view]);

  useLayoutEffect(() => {
    latestAnchor.current = anchorRequest;
  }, [anchorRequest]);

  function collapse() {
    setView(null);
    (view === "activity" ? activityLauncher.current : launcher.current)?.focus();
  }

  function selectThread(item: ConversationSummary) {
    revision.current += 1;
    setChosenId(item.id);
    setAgentId(item.agent_id ?? null);
    setDirectModel(!item.agent_id);
    setAnchor(null);
    setError("");
    setRecovering(false);
    setFocusComposer(false);
    setView("chat");
  }

  function startNew() {
    revision.current += 1;
    setChosenId(null);
    setAnchor(null);
    setTitle("");
    setError("");
    setRecovering(false);
    setFocusComposer(false);
  }

  function chooseAgent(profile: TeamAgent) {
    if (thread && profile.id === agent?.id) return;
    revision.current += 1;
    setAgentId(profile.id);
    setDirectModel(false);
    setError("");
    setRecovering(false);
    setFocusComposer(false);
    // Choosing another recipient opens their own conversation or a new draft, never retargeting the current one.
    if (thread) {
      const existing = threads.find(({ agent_id, claim_id }) => agent_id === profile.id && !claim_id);
      setChosenId(existing?.id ?? null);
      setAnchor(null);
      setTitle("");
    }
  }

  function chooseModel() {
    if (thread && !thread.agent_id) return;
    if (thread) startNew();
    setAgentId(null);
    setDirectModel(true);
  }

  async function create() {
    if ((!agent && !directModel) || busy.current) return;
    busy.current = true;
    const selected = revision.current;
    const startingAnchor = anchorRequest;
    const name = title.trim() || t("conversations.defaultTitle");
    const scope = anchor?.anchor ?? {};
    const key = JSON.stringify([agent?.id, scope, name]);
    const id = createRequest.current?.key === key ? createRequest.current.id : crypto.randomUUID();
    createRequest.current = { key, id };
    setCreating(true);
    setError("");
    try {
      const next = await actions.create(name, scope, agent?.id, id);
      createRequest.current = null;
      if (selected !== revision.current || latestAnchor.current !== startingAnchor) return;
      setChosenId(next.id);
      setFocusComposer(true);
      setRecovering(false);
    } catch (cause) {
      if (selected !== revision.current || latestAnchor.current !== startingAnchor) return;
      if (cause instanceof ApiError && (cause.code ?? cause.message) === "conversation.stale") {
        createRequest.current = null;
      }
      setError(conversationErrorText(t, cause));
      setRecovering(true);
    } finally {
      busy.current = false;
      setCreating(false);
    }
  }

  const threadPicker = (
    <Picker
      label={t("agentDock.threadPicker")}
      searchLabel={t("agentDock.searchThreads")}
      emptyLabel={t(conversations.isPending ? "common.loading" : "conversations.noConversations")}
      triggerClassName="agent-dock-picker-trigger"
      className="agent-dock-picker-panel"
      placement="top-start"
      value={thread?.id}
      trigger={
        <>
          <span>{thread?.title ?? t("agentDock.threads")}</span>
          <Chevron />
        </>
      }
      disabled={creating}
      groups={[
        {
          options: threads.map((item) => ({
            id: item.id,
            label: item.title,
            detail: [
              agentsById.get(item.agent_id ?? "")?.name,
              conversationStatus(item),
              timeText(i18n.language, item.updated_at, true),
            ]
              .filter(Boolean)
              .join(" · "),
            current: item.id === thread?.id,
            onSelect: () => selectThread(item),
          })),
        },
      ]}
    />
  );

  return (
    <footer className="agent-dock" aria-label={t("agentDock.title")}>
      <button
        ref={activityLauncher}
        type="button"
        className="agent-dock-status"
        aria-controls={panelId}
        aria-expanded={view === "activity"}
        onClick={() => open("activity")}
      >
        <span>{t("agentDock.activity")}</span>
        {runningCount > 0 && (
          <span data-tone="live">
            <Mark tone="ok" />
            {t("agentDock.running", { count: runningCount })}
          </span>
        )}
        {queuedWork.length > 0 && (
          <span>
            <Mark tone="planned" />
            {t("agentDock.queued", { count: queuedWork.length })}
          </span>
        )}
        {attentionCount > 0 && (
          <span data-tone="pending">
            <Mark tone="review" />
            {t("agentDock.attention", { count: attentionCount })}
          </span>
        )}
      </button>
      <button
        ref={launcher}
        className="agent-dock-launcher"
        type="button"
        aria-controls={panelId}
        aria-expanded={view === "chat"}
        onClick={() => open("chat")}
      >
        <svg viewBox="0 0 18 18" aria-hidden="true">
          <path d="M3 3.5h12v8H8l-3.5 3v-3H3z M6 7.5h6" />
        </svg>
        <span>{t("agentDock.title")}</span>
      </button>
      <section
        className="agent-dock-panel"
        id={panelId}
        aria-labelledby={titleId}
        hidden={view === null}
        onKeyDown={(event) => {
          if (
            event.key !== "Escape" ||
            event.defaultPrevented ||
            (event.target instanceof Element && event.target.closest("dialog[open]"))
          ) {
            return;
          }
          event.preventDefault();
          collapse();
        }}
      >
        <header className="agent-dock-heading">
          <h2 id={titleId}>{t(view === "activity" ? "agentDock.activity" : "agentDock.title")}</h2>
          <button
            className="icon-button"
            ref={closeControl}
            type="button"
            aria-label={t("agentDock.close")}
            title={t("agentDock.close")}
            onClick={collapse}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3.5 8h9" />
            </svg>
          </button>
        </header>
        {view === "activity" ? (
          <div className="agent-dock-activity">
            {(conversations.isError || work.isError) && <p role="status">{t("agentDock.activityUnavailable")}</p>}
            {runningCount + queuedWork.length + attentionCount === 0 ? (
              conversations.isPending || work.isPending ? (
                <p role="status">{t("common.loading")}</p>
              ) : (
                !conversations.isError && !work.isError && <p>{t("agentDock.noActivity")}</p>
              )
            ) : (
              <>
                {[...activeChats, ...failedChats].length > 0 && (
                  <>
                    <h3>{t("agentDock.threads")}</h3>
                    <ul>
                      {[...activeChats, ...failedChats].map((item) => (
                        <li key={item.id}>
                          <button type="button" onClick={() => selectThread(item)}>
                            <span>{item.title}</span>
                            <small>
                              {[agentsById.get(item.agent_id ?? "")?.name, conversationStatus(item)]
                                .filter(Boolean)
                                .join(" · ")}
                            </small>
                          </button>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
                {[...activeWork, ...attentionWork].length > 0 && (
                  <>
                    <h3>{t("agentWork.title")}</h3>
                    <ul>
                      {[...activeWork, ...attentionWork].map((task) => (
                        <li key={task.id}>
                          <a href={href("claims", "agent-work/" + task.id)} onClick={collapse}>
                            <span>{task.title}</span>
                            <small>
                              {agentsById.get(task.agent_id ?? "")?.name ?? task.agent_name} ·{" "}
                              {t(`agentWork.status.${task.status}`)}
                            </small>
                          </a>
                        </li>
                      ))}
                    </ul>
                  </>
                )}
              </>
            )}
          </div>
        ) : view === "chat" ? (
          <>
            <div className="agent-dock-toolbar">
              {threadPicker}
              <button className="conversation-text-action" type="button" onClick={startNew} disabled={creating}>
                {t("agentDock.newConversation")}
              </button>
            </div>
            <div className="agent-dock-recipient">
              <Picker
                label={t("agentDock.chooseAgent")}
                searchLabel={t("agentDock.searchAgents")}
                emptyLabel={t(profiles.isPending ? "common.loading" : "agentDock.noAgents")}
                triggerClassName="agent-dock-picker-trigger"
                className="agent-dock-picker-panel"
                placement="top-start"
                value={directModel ? "direct-model" : agent?.id}
                disabled={creating}
                trigger={
                  <>
                    {agent ? (
                      <>
                        <TeamAgentAvatar avatar={agent.avatar} name={agent.name} />
                        <span>{agent.name}</span>
                      </>
                    ) : (
                      <span>
                        {t(
                          directModel
                            ? "conversations.chooseModel"
                            : thread
                              ? "agentDock.unbound"
                              : "agentDock.chooseAgent",
                        )}
                      </span>
                    )}
                    <Chevron />
                  </>
                }
                groups={[
                  {
                    options: agents.map((item) => ({
                      id: item.id,
                      label: item.name,
                      icon: <TeamAgentAvatar avatar={item.avatar} name={item.name} />,
                      current: item.id === agent?.id,
                      onSelect: () => chooseAgent(item),
                    })),
                  },
                  {
                    options: [
                      {
                        id: "direct-model",
                        label: t("conversations.chooseModel"),
                        current: directModel,
                        onSelect: chooseModel,
                      },
                    ],
                  },
                ]}
              />
              {thread && (
                <a className="conversation-text-action" href={href("chat", thread.id)} onClick={collapse}>
                  {t("agentDock.openChats")}
                </a>
              )}
            </div>
            {thread?.claim_id && (
              <div className="agent-dock-scope">
                {anchor
                  ? t("agentDock.anchor", { title: anchor.title })
                  : t(thread.node_id ? "conversations.nodeScope" : "conversations.claimScope")}
              </div>
            )}
            {thread ? (
              duplicate ? (
                <p className="agent-dock-empty">{t("agentDock.alreadyOpen")}</p>
              ) : (
                <ConversationPanel
                  key={thread.id}
                  session={session}
                  thread={thread}
                  employee={agent}
                  focusComposer={focusComposer && view === "chat"}
                  onRemove={startNew}
                />
              )
            ) : (
              <div className="agent-dock-draft">
                {anchor ? (
                  <div className="agent-dock-anchor">
                    <span>{t("agentDock.anchor", { title: anchor.title })}</span>
                    <button
                      className="conversation-text-action"
                      type="button"
                      disabled={creating}
                      onClick={() => setAnchor(null)}
                    >
                      {t("agentDock.detach")}
                    </button>
                  </div>
                ) : (
                  <p>{t("agentDock.contextNone")}</p>
                )}
                {(agent || directModel) && (
                  <form
                    onSubmit={(event) => {
                      event.preventDefault();
                      void create();
                    }}
                  >
                    <label>
                      <span>{t("employeeChats.taskName")}</span>
                      <input
                        value={title}
                        onChange={(event) => setTitle(event.currentTarget.value)}
                        maxLength={200}
                        disabled={creating}
                      />
                    </label>
                    <button type="submit" className="button primary" disabled={creating}>
                      {creating && <Spin />}
                      {t(
                        creating
                          ? "agentDock.creating"
                          : recovering
                            ? "agentWork.retry"
                            : "agentDock.startConversation",
                      )}
                    </button>
                  </form>
                )}
                {error && <p role="alert">{error}</p>}
              </div>
            )}
          </>
        ) : null}
      </section>
    </footer>
  );
}
