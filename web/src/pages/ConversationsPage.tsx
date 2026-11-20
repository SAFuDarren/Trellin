import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  conversationErrorText,
  conversationRecipientLabel,
  useConversationActions,
  useConversations,
  type ConversationSummary,
} from "../api/conversations";
import { useThreads, type Thread } from "../api/sections";
import { ConversationPanel } from "../components/ConversationPanel";
import { FeedEntry } from "../components/FeedEntry";
import { Chevron } from "../components/Icons";
import { Empty, PageHead } from "../components/SectionLayout";
import { href } from "../shell/route";
import { nodeTitle, timeText } from "../workbench/text";
import "../styles/chats-page.css";

export function ConversationsPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string, nodeId?: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const proof = useThreads(session);
  const direct = useConversations(session);
  const actions = useConversationActions(session);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const actionMenu = useRef<HTMLDetailsElement>(null);
  const conversations = direct.data?.threads ?? [];
  const showProof = sub.includes("|");
  const current = showProof ? undefined : sub ? conversations.find((item) => item.id === sub) : conversations[0];
  const proofRows = proof.data?.threads ?? [];
  const proofKey = (thread: (typeof proofRows)[number]) => thread.claim_id + "|" + thread.node_id;
  const selectedProof = showProof ? proofRows.find((thread) => proofKey(thread) === sub) : undefined;
  const back = (
    <a href={href("chat")} className="chat-back">
      <Chevron />
      {t("conversations.backToList")}
    </a>
  );

  useEffect(() => {
    function closeActions(event: PointerEvent) {
      const menu = actionMenu.current;
      if (menu?.open && event.target instanceof Node && !menu.contains(event.target)) menu.open = false;
    }
    document.addEventListener("pointerdown", closeActions);
    return () => document.removeEventListener("pointerdown", closeActions);
  }, []);

  async function create() {
    setCreating(true);
    setCreateError("");
    try {
      const thread = await actions.create(t("conversations.defaultTitle"), {});
      window.location.hash = href("chat", thread.id);
    } catch (error) {
      setCreateError(conversationErrorText(t, error));
    } finally {
      setCreating(false);
    }
  }

  function row(thread: ConversationSummary) {
    const turn = thread.latest_turn;
    const model = turn ? conversationRecipientLabel(t, turn.provider, turn.model ?? turn.requested_model) : "";
    const status = thread.running
      ? t("conversations.working")
      : turn?.status === "failed"
        ? t("conversations.lastSendFailed")
        : turn?.status === "answered"
          ? t("conversations.replied")
          : "";
    const at = turn?.at ?? thread.updated_at;
    return (
      <li key={thread.id}>
        <a
          className="chat-list-item"
          href={href("chat", thread.id)}
          aria-current={current?.id === thread.id ? "page" : undefined}
        >
          <span className="chat-list-item-heading">
            <strong title={thread.title}>{thread.title}</strong>
            <time dateTime={at}>{timeText(i18n.language, at, true)}</time>
          </span>
          {(model || status) && (
            <span className="chat-list-info">
              {model && (
                <span className="chat-list-model" title={model}>
                  {model}
                </span>
              )}
              {status && (
                <span className="chat-list-status" data-state={turn?.status}>
                  {status}
                </span>
              )}
            </span>
          )}
          <span className="chat-list-preview" title={turn?.preview}>
            {turn?.preview || t("conversations.emptyThread")}
          </span>
        </a>
      </li>
    );
  }

  return (
    <div className="chats-page" data-thread={String(Boolean(sub))}>
      <nav className="chats-sidebar" aria-labelledby="section-title">
        <header className="chat-list-head">
          <h1 id="section-title">{t("rail.chat")}</h1>
          <button
            type="button"
            className="icon-button"
            disabled={creating || direct.isPending || direct.isError}
            aria-label={t("conversations.new")}
            title={t("conversations.new")}
            onClick={() => void create()}
          >
            <svg viewBox="0 0 16 16" aria-hidden="true">
              <path d="M3.5 8h9 M8 3.5v9" />
            </svg>
          </button>
        </header>
        <div className="chat-list-scroll">
          {createError && (
            <p className="chat-list-note" role="alert">
              {createError}
            </p>
          )}
          {direct.isPending ? (
            <p className="chat-list-note" role="status">
              {t("common.loading")}
            </p>
          ) : direct.isError ? (
            <p className="chat-list-note" role="alert">
              {conversationErrorText(t, direct.error)}
            </p>
          ) : conversations.length ? (
            <ul aria-label={t("conversations.threads")}>{conversations.map(row)}</ul>
          ) : (
            <p className="chat-list-note">{t("conversations.noConversations")}</p>
          )}
          {proofRows.length > 0 && (
            <section className="chat-proof-list" aria-label={t("conversations.proofActivity")}>
              <h2>{t("conversations.proofActivity")}</h2>
              <ul>
                {proofRows.map((thread) => (
                  <li key={proofKey(thread)}>
                    <a
                      className="chat-list-item"
                      href={href("chat", proofKey(thread))}
                      aria-current={thread === selectedProof ? "page" : undefined}
                    >
                      <span className="chat-list-item-heading">
                        <strong>{nodeTitle(t, thread.node)}</strong>
                        {thread.last_at && (
                          <time dateTime={thread.last_at}>{timeText(i18n.language, thread.last_at, true)}</time>
                        )}
                      </span>
                      {thread.claim_title && <span className="chat-list-preview">{thread.claim_title}</span>}
                      {thread.live > 0 && (
                        <span className="chat-list-status" data-state="running">
                          {t("threads.running", { count: thread.live })}
                        </span>
                      )}
                    </a>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      </nav>
      <main className="chats-main" tabIndex={-1}>
        {showProof && selectedProof ? (
          <ProofHistory key={proofKey(selectedProof)} thread={selectedProof} onOpen={onOpen} back={back} />
        ) : !showProof && current ? (
          <ConversationPanel
            key={session + "/" + current.id}
            session={session}
            thread={current}
            onRemove={() => {
              window.location.hash = href("chat");
            }}
            header={(remove) => (
              <PageHead
                title={current.title}
                note={t(
                  current.node_id
                    ? "conversations.nodeScope"
                    : current.claim_id
                      ? "conversations.claimScope"
                      : "conversations.projectScope",
                )}
                actions={
                  <>
                    {back}
                    <details
                      className="chats-actions"
                      ref={actionMenu}
                      onKeyDown={(event) => {
                        if (event.key !== "Escape" || !event.currentTarget.open) return;
                        event.preventDefault();
                        event.currentTarget.open = false;
                        event.currentTarget.querySelector("summary")?.focus();
                      }}
                    >
                      <summary title={t("conversations.actions")} aria-label={t("conversations.actions")}>
                        <svg viewBox="0 0 18 18" aria-hidden="true">
                          <path d="M4 9h.1 M9 9h.1 M14 9h.1" />
                        </svg>
                      </summary>
                      <div
                        className="chats-actions-list"
                        onClick={(event) => {
                          if (!(event.target instanceof Element) || !event.target.closest("button")) return;
                          const disclosure = event.currentTarget.closest("details");
                          if (disclosure) {
                            disclosure.open = false;
                            disclosure.querySelector("summary")?.focus();
                          }
                        }}
                      >
                        {current.claim_id && (
                          <button
                            type="button"
                            className="conversation-text-action"
                            onClick={() => onOpen(current.claim_id!, current.node_id ?? undefined)}
                          >
                            {t("common.openInWorkbench")}
                          </button>
                        )}
                        {remove}
                      </div>
                    </details>
                  </>
                }
              />
            )}
          />
        ) : (
          <>
            <PageHead title={t("rail.chat")} actions={back} />
            <Empty>
              {direct.isError && !showProof
                ? conversationErrorText(t, direct.error)
                : proof.isError && showProof
                  ? conversationErrorText(t, proof.error)
                  : direct.isPending || (showProof && proof.isPending)
                    ? t("common.loading")
                    : sub
                      ? t("conversations.conversationMissing")
                      : t("conversations.emptyProject")}
            </Empty>
          </>
        )}
      </main>
    </div>
  );
}

function ProofHistory({
  thread,
  onOpen,
  back,
}: {
  thread: Thread;
  onOpen: (claimId: string, nodeId?: string) => void;
  back: ReactNode;
}) {
  const { t } = useTranslation();
  const [model, setModel] = useState("all");
  const events = thread.events.filter(
    (event) => model === "all" || (model === "you" ? event.kind === "guidance" : event.model === model),
  );
  return (
    <div className="chats-proof-history">
      <PageHead
        title={nodeTitle(t, thread.node)}
        note={thread.claim_title ?? t("conversations.proofActivity")}
        actions={
          <>
            {back}
            <button
              type="button"
              className="conversation-text-action"
              onClick={() => onOpen(thread.claim_id, thread.node_id)}
            >
              {t("common.openInWorkbench")}
            </button>
          </>
        }
      />
      <div className="thread-filters" role="group" aria-label={t("threads.filter")}>
        {["all", ...thread.models, "you"].map((value) => (
          <button
            key={value}
            type="button"
            className="chip"
            aria-pressed={model === value}
            onClick={() => setModel(value)}
          >
            {value === "all" ? t("threads.allModels") : value === "you" ? t("threads.yourNotes") : value}
          </button>
        ))}
      </div>
      <ol className="chat-feed thread-feed" tabIndex={0} aria-label={t("chat.feed")}>
        {events.length ? (
          events.map((event) => <FeedEntry key={event.id} record={event} />)
        ) : (
          <li className="chat-empty">{t("threads.empty")}</li>
        )}
      </ol>
    </div>
  );
}
