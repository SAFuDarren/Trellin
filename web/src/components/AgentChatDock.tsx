import { useId, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import type { ConversationSummary } from "../api/conversations";
import { useTeamAgents } from "../api/teamAgents";
import {
  DEFAULT_CHAT_SIZE,
  readAgentChatSize,
  saveAgentChatSize,
  useAgentChatArea,
  type ChatSize,
} from "../shell/agentChatGeometry";
import { useConversationSending } from "../shell/ConversationEditors";
import { href, type Route } from "../shell/route";
import { reviewItemHref } from "../shell/reviewItems";
import type { ActivityState, WorkspaceActivity } from "../shell/workspaceActivity";
import { timeText } from "../workbench/text";
import { AgentChatResizeHandles } from "./AgentChatResizeHandles";
import { ConversationPanel } from "./ConversationPanel";
import { Chevron, Mark } from "./Icons";
import { Picker } from "./FloatingControls";
import { DIRECT_MODEL, type ConversationStarterScope } from "./ConversationRecipients";
import { NewConversation, type DraftAnchor } from "./NewConversation";
import { WorkspaceActivityList } from "./WorkspaceActivity";
import "../styles/agent-chat-dock.css";

export type AgentChatAnchor = DraftAnchor;
export type AgentChatRequest = AgentChatAnchor | { newConversation: true };

type AgentDockView = "chat" | "activity";

export function AgentChatDock({
  session,
  route,
  request,
  starterScope,
  activity,
  activityLoading,
  activityUnavailable,
  conversations: threads,
  conversationsLoading,
  onOpenProof,
}: {
  session: string;
  route: Route;
  request: AgentChatRequest | null;
  starterScope?: ConversationStarterScope;
  activity: WorkspaceActivity;
  activityLoading: boolean;
  activityUnavailable: boolean;
  conversations: ConversationSummary[];
  conversationsLoading: boolean;
  onOpenProof: (claimId: string, nodeId?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const sending = useConversationSending(session);
  const [view, setView] = useState<AgentDockView | null>(null);
  const [activityFilter, setActivityFilter] = useState<ActivityState | "all">("all");
  const activityOrigin = useRef<HTMLButtonElement | null>(null);
  const [handledRequest, setHandledRequest] = useState<AgentChatRequest | null>(null);
  const [anchor, setAnchor] = useState<AgentChatAnchor | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [initialRecipient, setInitialRecipient] = useState<string>();
  const [focusComposer, setFocusComposer] = useState(false);
  const launcher = useRef<HTMLButtonElement>(null);
  const footer = useRef<HTMLElement>(null);
  const panelElement = useRef<HTMLElement>(null);
  const [chatSize, setChatSize] = useState(readAgentChatSize);
  const [maximized, setMaximized] = useState(false);
  const area = useAgentChatArea(footer, view === "chat", route.section, route.sub);
  const preferredSize = chatSize ?? DEFAULT_CHAT_SIZE;
  const floatStyle: CSSProperties & { "--agent-chat-height": string } = {
    right: area.right,
    bottom: area.bottom,
    width: maximized ? area.width : Math.min(area.width, preferredSize.width),
    "--agent-chat-height": `${maximized ? area.height : Math.min(area.height, preferredSize.height)}px`,
    maxHeight: area.height,
  };
  const activityLauncher = useRef<HTMLButtonElement>(null);
  const closeControl = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const titleId = useId();

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
  const profiles = useTeamAgents(session);
  const agents = profiles.data?.agents ?? [];
  const agentsById = new Map(agents.map((profile) => [profile.id, profile]));
  const thread = threads.find(({ id }) => id === chosenId);
  const agent = agentsById.get(thread?.agent_id ?? "");
  const { running, queued, review, attention } = activity.counts;
  const onlyReview =
    review === 1 ? activity.rows.find((row) => row.source === "review" && row.state === "review") : undefined;

  if (request && request !== handledRequest) {
    const anchored = "anchor" in request ? request : null;
    setHandledRequest(request);
    setAnchor(anchored);
    setChosenId(null);
    setInitialRecipient(undefined);
    setFocusComposer(true);
    if (route.section !== "chat") setView("chat");
  }
  if (route.section === "chat" && view === "chat") setView(null);

  function open(kind: AgentDockView) {
    if (kind === "chat" && route.section === "chat") {
      setView(null);
      const input = document.querySelector<HTMLTextAreaElement>(".chats-main .direct-composer textarea:not(:disabled)");
      if (input) input.focus();
      else window.location.hash = href("chat", "new");
      return;
    }
    if (view === kind) {
      setView(null);
      return;
    }
    setFocusComposer(kind === "chat");
    setView(kind);
  }

  function commitSize(size: ChatSize) {
    setChatSize(size);
    saveAgentChatSize(size);
  }

  useLayoutEffect(() => {
    if (view === "activity") closeControl.current?.focus();
  }, [view]);

  function openActivity(filter: "all" | "review", trigger: HTMLButtonElement) {
    if (view === "activity" && activityOrigin.current !== trigger) {
      setActivityFilter(filter);
      activityOrigin.current = trigger;
      closeControl.current?.focus();
      return;
    }
    setActivityFilter(filter);
    activityOrigin.current = trigger;
    open("activity");
  }

  function collapse() {
    setView(null);
    const origin = view === "activity" ? activityOrigin.current : launcher.current;
    (origin?.isConnected ? origin : activityLauncher.current)?.focus();
  }

  function selectThread(item: ConversationSummary) {
    setChosenId(item.id);
    setInitialRecipient(undefined);
    setAnchor(null);
    setFocusComposer(true);
    if (route.section === "chat") {
      setView(null);
      window.location.hash = href("chat", item.id);
    } else {
      setView("chat");
    }
  }

  function startNew(recipient?: string) {
    setChosenId(null);
    setAnchor(null);
    setInitialRecipient(recipient);
    setFocusComposer(true);
  }

  function switchRecipient(id: string) {
    if (id === (thread?.agent_id ?? DIRECT_MODEL)) return;
    const existing = threads.find(({ agent_id, claim_id }) => agent_id === id && !claim_id);
    if (existing) selectThread(existing);
    else startNew(id);
  }

  const threadPicker = (
    <Picker
      label={t("agentDock.threadPicker")}
      searchLabel={t("agentDock.searchThreads")}
      emptyLabel={t(conversationsLoading ? "common.loading" : "conversations.noConversations")}
      triggerClassName="agent-dock-picker-trigger"
      className="agent-dock-picker-panel"
      placement="top-start"
      value={thread?.id}
      trigger={
        <>
          <span>{thread?.title ?? t("agentDock.newConversation")}</span>
          <Chevron />
        </>
      }
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

  const panel = (
    <section
      ref={panelElement}
      className="agent-dock-panel"
      data-maximized={view === "chat" && maximized ? "true" : undefined}
      data-user-size={view === "chat" && chatSize ? "true" : undefined}
      style={view === "chat" ? floatStyle : undefined}
      data-view={view ?? undefined}
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
      {view === "chat" && !maximized && (
        <AgentChatResizeHandles
          panel={panelElement}
          preferred={chatSize}
          available={area}
          onSize={setChatSize}
          onCommit={commitSize}
        />
      )}
      <header className="agent-dock-heading">
        <h2 id={titleId} className={view === "chat" ? "sr-only" : undefined}>
          {t(view === "activity" ? "agentDock.activity" : "agentDock.title")}
        </h2>
        {view === "chat" && (
          <>
            {threadPicker}
            <div className="agent-dock-heading-actions">
              <button
                className="icon-button"
                type="button"
                aria-label={t("agentDock.newConversation")}
                title={t("agentDock.newConversation")}
                onClick={() => startNew()}
              >
                <svg viewBox="0 0 16 16" aria-hidden="true">
                  <path d="M3.5 8h9 M8 3.5v9" />
                </svg>
              </button>
              {thread && (
                <a className="conversation-text-action" href={href("chat", thread.id)}>
                  {t("agentDock.openChats")}
                </a>
              )}
            </div>
          </>
        )}
        {view === "activity" && (
          // #294: the drawer is Agent activity's one entry point; this opens its full page.
          <div className="agent-dock-heading-actions">
            <a
              className="conversation-text-action"
              href={href("agent-work", "activity")}
              onClick={(event) => {
                if (!(event.metaKey || event.ctrlKey || event.shiftKey || event.altKey)) setView(null);
              }}
            >
              {t("agentDock.openActivity")}
            </a>
          </div>
        )}
        {view === "chat" && (
          <button
            className="icon-button"
            type="button"
            aria-label={t(maximized ? "agentDock.restoreSize" : "agentDock.maximize")}
            title={t(maximized ? "agentDock.restoreSize" : "agentDock.maximize")}
            onClick={() => setMaximized((current) => !current)}
          >
            <svg className="workbench-icon" viewBox="0 0 24 24" aria-hidden="true">
              <path d={maximized ? "M4 9h5V4m11 11h-5v5M9 9 4 4m11 11 5 5" : "M9 4H4v5m11 11h5v-5M4 4l5 5m11 11-5-5"} />
            </svg>
          </button>
        )}
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
        <WorkspaceActivityList
          activity={activity}
          agents={agentsById}
          selectedFilter={activityFilter}
          onFilter={setActivityFilter}
          loading={activityLoading}
          unavailable={activityUnavailable}
          onNavigate={() => setView(null)}
          onOpenProof={onOpenProof}
          onOpenThread={selectThread}
        />
      ) : view === "chat" ? (
        <>
          {thread?.claim_id && (
            <div className="agent-dock-scope">
              {anchor
                ? t("agentDock.anchor", { title: anchor.title })
                : t(thread.node_id ? "conversations.nodeScope" : "conversations.claimScope")}
            </div>
          )}
          {thread ? (
            <ConversationPanel
              key={thread.id}
              session={session}
              thread={thread}
              employee={agent}
              recipientChoice={{ agents, pending: profiles.isPending, onSelect: switchRecipient }}
              focusComposer={focusComposer}
              onRemove={() => startNew()}
            />
          ) : (
            <NewConversation
              session={session}
              agents={agents}
              agentsPending={profiles.isPending}
              agentsError={profiles.isError}
              anchor={anchor}
              starterScope={starterScope}
              onDetach={() => setAnchor(null)}
              initialRecipient={initialRecipient}
              onCreated={(item) => {
                setChosenId(item.id);
                setFocusComposer(true);
              }}
              focusComposer={focusComposer}
            />
          )}
        </>
      ) : null}
    </section>
  );

  return (
    <footer ref={footer} className="agent-dock" aria-label={t("agentDock.title")}>
      <button
        ref={activityLauncher}
        type="button"
        className="agent-dock-status"
        aria-controls={panelId}
        aria-expanded={view === "activity" && activityOrigin.current === activityLauncher.current}
        onClick={(event) => openActivity("all", event.currentTarget)}
      >
        <span>{t("agentDock.activity")}</span>
        {running > 0 && (
          <span data-tone="live">
            <Mark tone="ok" />
            {t("agentDock.running", { count: running })}
          </span>
        )}
        {queued > 0 && (
          <span>
            <Mark tone="planned" />
            {t("agentDock.queued", { count: queued })}
          </span>
        )}
        {attention > 0 && (
          <span>
            <Mark tone="bad" />
            {t("workspaceActivity.attention", { count: attention })}
          </span>
        )}
      </button>
      {onlyReview?.source === "review" ? (
        <a
          className="agent-dock-status agent-dock-review"
          href={reviewItemHref(onlyReview.item)}
          onClick={(event) => {
            if (!event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey) setView(null);
          }}
        >
          <Mark tone="review" />
          {t("workspaceActivity.toReview", { count: review })}
        </a>
      ) : review > 0 ? (
        <button
          type="button"
          className="agent-dock-status agent-dock-review"
          aria-controls={panelId}
          aria-expanded={view === "activity" && activityOrigin.current !== activityLauncher.current}
          onClick={(event) => openActivity("review", event.currentTarget)}
        >
          <Mark tone="review" />
          {t("workspaceActivity.toReview", { count: review })}
        </button>
      ) : null}
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
      {panel}
    </footer>
  );
}
