import { useState } from "react";
import { useTranslation } from "react-i18next";
import {
  conversationErrorText,
  useConversationActions,
  useConversations,
  type ConversationAnchor,
  type ConversationSummary,
} from "../api/conversations";
import { timeText } from "../workbench/text";
import { ConversationPanel } from "./ConversationPanel";

export function WorkbenchConversations({
  session,
  anchor,
  title,
}: {
  session: string;
  anchor: ConversationAnchor;
  title: string;
}) {
  const { t, i18n } = useTranslation();
  const threads = useConversations(session, anchor.claim_id);
  const actions = useConversationActions(session);
  const [chosen, setChosen] = useState<ConversationSummary | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const scoped = (threads.data?.threads ?? []).filter(
    (thread) =>
      (thread.claim_id ?? undefined) === anchor.claim_id &&
      (thread.statement_id ?? undefined) === anchor.statement_id &&
      (thread.node_id ?? undefined) === anchor.node_id,
  );
  const selected = scoped.find((thread) => thread.id === chosen?.id) ?? scoped[0];

  async function create() {
    setCreating(true);
    setError("");
    try {
      setChosen(await actions.create(title.slice(0, 160), anchor));
    } catch (cause) {
      setError(conversationErrorText(t, cause));
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <div className="conversation-tools">
        {scoped.length > 1 && (
          <select
            aria-label={t("conversations.threads")}
            value={selected?.id ?? ""}
            onChange={(event) => setChosen(scoped.find((item) => item.id === event.target.value) ?? null)}
          >
            {scoped.map((item) => (
              <option key={item.id} value={item.id}>
                {item.title} · {timeText(i18n.language, item.updated_at, true)}
              </option>
            ))}
          </select>
        )}
        <button
          type="button"
          className="button"
          disabled={threads.isPending || threads.isError || creating}
          onClick={() => void create()}
        >
          {t("conversations.new")}
        </button>
        {(threads.isError || error) && <p role="alert">{error || conversationErrorText(t, threads.error)}</p>}
      </div>
      {threads.isPending ? (
        <p role="status">{t("common.loading")}</p>
      ) : selected ? (
        <ConversationPanel key={selected.id} session={session} thread={selected} onRemove={() => setChosen(null)} />
      ) : (
        !threads.isError && <p className="chat-empty">{t("conversations.emptyScope")}</p>
      )}
    </>
  );
}
