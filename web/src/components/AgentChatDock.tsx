import { useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConversationSummary } from "../api/conversations";
import { useTeamAgents } from "../api/teamAgents";
import { useConversationSending } from "../shell/ConversationEditors";
import { href, type Route } from "../shell/route";
import type { WorkspaceActivity } from "../shell/workspaceActivity";
import { timeText } from "../workbench/text";
import { ConversationPanel } from "./ConversationPanel";
import { Chevron, Mark } from "./Icons";
import { Picker } from "./FloatingControls";
import { DIRECT_MODEL } from "./ConversationRecipients";
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
  const [handledRequest, setHandledRequest] = useState<AgentChatRequest | null>(null);
  const [anchor, setAnchor] = useState<AgentChatAnchor | null>(null);
  const [chosenId, setChosenId] = useState<string | null>(null);
  const [initialRecipient, setInitialRecipient] = useState<string>();
  const [focusComposer, setFocusComposer] = useState(false);
  const launcher = useRef<HTMLButtonElement>(null);
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

  useLayoutEffect(() => {
    if (view === "activity") closeControl.current?.focus();
  }, [view]);

  function collapse() {
    setView(null);
    (view === "activity" ? activityLauncher.current : launcher.current)?.focus();
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
        {review > 0 && (
          <span data-tone="pending">
            <Mark tone="review" />
            {t("workspaceActivity.toReview", { count: review })}
          </span>
        )}
        {attention > 0 && (
          <span>
            <Mark tone="bad" />
            {t("workspaceActivity.attention", { count: attention })}
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
    </footer>
  );
}
