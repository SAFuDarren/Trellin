import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useThreads } from "../api/sections";
import { conversationErrorText, useConversationActions, useConversations } from "../api/conversations";
import { ConversationPanel } from "../components/ConversationPanel";
import { FeedEntry } from "../components/FeedEntry";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { nodeTitle, timeText } from "../workbench/text";

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
  const threads = useThreads(session);
  const direct = useConversations(session);
  const actions = useConversationActions(session);
  const [model, setModel] = useState("all");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState("");
  const conversations = direct.data?.threads ?? [];
  const showProof = sub.includes("|");
  const current = showProof ? undefined : (conversations.find((item) => item.id === sub) ?? conversations[0]);

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

  const newChat = (
    <button
      type="button"
      className="button primary"
      disabled={creating || direct.isPending || direct.isError}
      onClick={() => void create()}
    >
      {t("conversations.new")}
    </button>
  );
  const list = threads.data?.threads ?? [];
  const key = (thread: (typeof list)[number]) => thread.claim_id + "|" + thread.node_id;
  const selected = list.find((thread) => key(thread) === sub) ?? list[0];
  const entry = (thread: (typeof list)[number]) => ({
    key: key(thread),
    label: nodeTitle(t, thread.node),
    sub: [
      thread.claim_title,
      thread.live ? t("threads.running", { count: thread.live }) : timeText(i18n.language, thread.last_at, true),
    ]
      .filter(Boolean)
      .join(" · "),
    href: href("chat", key(thread)),
    current: showProof && thread === selected,
    dot: (thread.live ? "live" : "none") as "live" | "none",
  });
  const events = (selected?.events ?? [])
    .map((event) => ({ ...event, kind: event.kind }))
    .filter((event) => model === "all" || (model === "you" ? event.kind === "guidance" : event.model === model));
  return (
    <SectionLayout
      title={t("rail.chat")}
      groups={[
        {
          name: t("conversations.threads"),
          entries: conversations.map((item) => ({
            key: item.id,
            label: item.title,
            sub: timeText(i18n.language, item.updated_at, true),
            href: href("chat", item.id),
            current: item === current,
            dot: item.running ? "live" : "none",
          })),
        },
        {
          name: t("threads.active"),
          entries: list.filter((thread) => thread.live).map(entry),
          empty: t("threads.noneActive"),
        },
        { name: t("conversations.proofActivity"), entries: list.filter((thread) => !thread.live).map(entry) },
      ]}
    >
      {createError && (
        <p className="page-notice" role="alert">
          {createError}
        </p>
      )}
      {!showProof ? (
        <div className="direct-conversation-page">
          <PageHead title={current?.title ?? t("rail.chat")} actions={newChat} />
          {direct.isError ? (
            <Empty>{conversationErrorText(t, direct.error)}</Empty>
          ) : direct.isPending ? (
            <Empty>{t("common.loading")}</Empty>
          ) : current ? (
            <ConversationPanel
              key={session + "/" + current.id}
              session={session}
              thread={current}
              onRemove={() => {
                window.location.hash = href("chat");
              }}
            />
          ) : (
            <Empty>{t("conversations.emptyProject")}</Empty>
          )}
        </div>
      ) : threads.isError ? (
        <Empty>{threads.error.message}</Empty>
      ) : !selected ? (
        <>
          <PageHead title={t("rail.chat")} actions={newChat} />
          <Empty>{threads.isPending ? t("common.loading") : t("threads.none")}</Empty>
        </>
      ) : (
        <div className="feed-page">
          <PageHead
            title={nodeTitle(t, selected.node)}
            note={selected.claim_title ?? ""}
            actions={
              <>
                {newChat}
                <button
                  type="button"
                  className="button primary"
                  onClick={() => onOpen(selected.claim_id, selected.node_id)}
                >
                  {t("threads.reply")}
                </button>
              </>
            }
          />
          <div className="thread-filters" role="group" aria-label={t("threads.filter")}>
            {["all", ...selected.models, "you"].map((value) => (
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
          <ol className="chat-feed thread-feed" aria-label={t("chat.feed")}>
            {events.length ? (
              events.map((event) => <FeedEntry key={event.id} record={event} />)
            ) : (
              <li className="chat-empty">{t("threads.empty")}</li>
            )}
          </ol>
        </div>
      )}
    </SectionLayout>
  );
}
