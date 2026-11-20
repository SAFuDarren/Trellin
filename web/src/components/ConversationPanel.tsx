import { useId, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  conversationErrorText,
  useConversation,
  useConversationActions,
  type ConversationMessage,
  type ConversationPreview,
  type ConversationRequest,
  type ConversationSummary,
} from "../api/conversations";
import {
  connectionChoiceLabel,
  connectionModelLabel,
  dispatchableConnection,
  useModelConnections,
} from "../api/modelConnections";
import { useLocalTextModels } from "../api/localModels";
import { href } from "../shell/route";
import { timeText } from "../workbench/text";

export function ConversationPanel({
  session,
  thread,
  onRemove,
}: {
  session: string;
  thread: ConversationSummary;
  onRemove: () => void;
}) {
  const { t, i18n } = useTranslation();
  const detail = useConversation(session, thread.id);
  const actions = useConversationActions(session);
  const models = useModelConnections(session);
  const local = useLocalTextModels(session);
  const options = [
    ...(local.data?.models ?? []).map((name) => ({
      id: "local:" + name,
      label: t("conversations.localModel", { name }),
      local: true,
    })),
    ...(models.data?.connections ?? []).filter(dispatchableConnection).map((connection) => {
      const choice = connectionChoiceLabel(t, connection);
      const model = connectionModelLabel(t, connection);
      const label = choice.endsWith(" · " + model) ? choice : choice + " · " + model;
      return { id: connection.id, label, local: false };
    }),
  ];
  const [selected, setSelected] = useState("");
  const [text, setText] = useState("");
  const [pending, setPending] = useState<"preview" | "send" | "archive" | null>(null);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState(false);
  const [approval, setApproval] = useState<{ request: ConversationRequest; preview: ConversationPreview } | null>(null);
  const request = useRef<ConversationRequest | null>(null);
  const busy = useRef(false);
  const formId = useId();
  const feed = useRef<HTMLOListElement>(null);
  const messages = detail.data?.thread.messages ?? [];
  const running = detail.data?.thread.running ?? thread.running;
  const connection = options.find((option) => option.id === selected);
  const disabled = Boolean(pending || running || approval || removing);

  useLayoutEffect(() => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [messages.length]);

  function errorText(code: string) {
    return t(`errors.${code}`, { defaultValue: t("conversations.failed") });
  }

  async function post(next: ConversationRequest, token: string, external: boolean) {
    setPending("send");
    await actions.send(next, token, external);
    setText("");
    request.current = null;
  }

  async function review(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!connection || !text.trim() || disabled || busy.current) return;
    const previous = request.current;
    const body =
      previous?.text === text.trim() && previous.connection_id === selected
        ? previous
        : { thread_id: thread.id, connection_id: selected, text: text.trim(), request_id: crypto.randomUUID() };
    request.current = body;
    busy.current = true;
    setError("");
    setPending("preview");
    try {
      const preview = await actions.preview(body);
      if (preview.external) {
        setApproval({ request: body, preview });
      } else if (preview.external === false && connection.local) {
        await post(body, preview.token, false);
      } else {
        setError(t("conversations.failed"));
      }
    } catch (cause) {
      setError(conversationErrorText(t, cause));
    } finally {
      busy.current = false;
      setPending(null);
    }
  }

  async function confirm() {
    if (!approval || running || busy.current) return;
    busy.current = true;
    setError("");
    try {
      await post(approval.request, approval.preview.token, true);
    } catch (cause) {
      setError(conversationErrorText(t, cause));
    } finally {
      setApproval(null);
      setPending(null);
      busy.current = false;
    }
  }

  async function archive() {
    if (running || busy.current) return;
    busy.current = true;
    setPending("archive");
    setError("");
    try {
      await actions.archive(thread.id);
      onRemove();
    } catch (cause) {
      setError(conversationErrorText(t, cause));
    } finally {
      busy.current = false;
      setRemoving(false);
      setPending(null);
    }
  }

  const recipient = (provider: string, model: string) =>
    provider === "ollama-native"
      ? t("conversations.localModel", { name: model })
      : [
          t(`team.providers.${provider}`, { defaultValue: provider }),
          connectionModelLabel(t, { provider, model }),
        ].join(" · ");

  function speaker(message: ConversationMessage): string {
    if (message.role === "user") return t("conversations.you");
    if (message.provider && message.model) return recipient(message.provider, message.model);
    if (message.provider && message.requested_model) {
      return t("conversations.requestedModel", { name: recipient(message.provider, message.requested_model) });
    }
    return t("model.fallback");
  }
  return (
    <section className="conversation-panel" aria-label={thread.title}>
      {detail.isPending ? (
        <p role="status">{t("common.loading")}</p>
      ) : detail.isError ? (
        <p role="alert">{conversationErrorText(t, detail.error)}</p>
      ) : (
        <ol className="chat-feed" ref={feed} aria-label={t("chat.feed")}>
          {messages.length === 0 && <li className="chat-empty">{t("conversations.emptyThread")}</li>}
          {messages.map((message) => (
            <li key={message.id} data-from={message.role === "user" ? "you" : "model"}>
              <span className="speaker">
                <b>{speaker(message)}</b>
                {message.at && <time dateTime={message.at}>{timeText(i18n.language, message.at)}</time>}
              </span>
              <p className={message.role === "user" ? "bubble" : "message"}>{message.text}</p>
            </li>
          ))}
        </ol>
      )}
      <form className="composer" onSubmit={(event) => void review(event)}>
        <label className="sr-only" htmlFor={formId + "-text"}>
          {t("conversations.message")}
        </label>
        <textarea
          id={formId + "-text"}
          value={text}
          rows={2}
          maxLength={1500}
          disabled={disabled || !detail.data}
          placeholder={t("conversations.message")}
          onChange={(event) => {
            setText(event.target.value);
            request.current = null;
            setError("");
          }}
        />
        <div className="composer-row">
          <label className="sr-only" htmlFor={formId + "-model"}>
            {t("conversations.model")}
          </label>
          <select
            id={formId + "-model"}
            value={connection?.id ?? ""}
            disabled={disabled || !options.length}
            onChange={(event) => {
              setSelected(event.target.value);
              request.current = null;
              setError("");
            }}
          >
            <option value="">{t("conversations.chooseModel")}</option>
            {options.map((option) => (
              <option key={option.id} value={option.id}>
                {option.label}
              </option>
            ))}
          </select>
          {!models.isPending && !local.isPending && !options.length && (
            <a href={href("team", "runtimes")}>{t("conversations.connect")}</a>
          )}
          <button
            type="submit"
            className="button primary"
            disabled={disabled || !detail.data || !connection || !text.trim()}
          >
            {pending === "preview" || pending === "send" || running
              ? t("conversations.working")
              : t("conversations.send")}
          </button>
          <button type="button" className="button" disabled={disabled} onClick={() => setRemoving(true)}>
            {t("conversations.remove")}
          </button>
        </div>
        {models.isError && <p role="alert">{t("modelConnections.failed")}</p>}
        {(error || detail.data?.thread.latest_error) && (
          <p className="composer-status" data-error="true" role="alert">
            {error || errorText(detail.data?.thread.latest_error ?? "")}
          </p>
        )}
      </form>
      {approval && (
        <Confirmation
          title={t("conversations.reviewTitle")}
          confirmLabel={t("conversations.confirmSend")}
          pending={Boolean(pending)}
          blocked={running}
          onCancel={() => setApproval(null)}
          onConfirm={() => void confirm()}
        >
          <p>{recipient(approval.preview.provider, approval.preview.model)}</p>
          <pre tabIndex={0}>{approval.preview.outgoing}</pre>
        </Confirmation>
      )}
      {removing && (
        <Confirmation
          title={t("conversations.remove")}
          confirmLabel={t("conversations.confirmRemove")}
          pending={Boolean(pending)}
          blocked={running}
          onCancel={() => setRemoving(false)}
          onConfirm={() => void archive()}
        >
          <p>{t("conversations.removeHint")}</p>
        </Confirmation>
      )}
    </section>
  );
}

function Confirmation({
  title,
  confirmLabel,
  pending,
  blocked,
  onCancel,
  onConfirm,
  children,
}: {
  title: string;
  confirmLabel: string;
  pending: boolean;
  blocked: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useLayoutEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      className="conversation-confirmation"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) onCancel();
      }}
    >
      <h3 id={titleId}>{title}</h3>
      {children}
      <div className="choices">
        <button type="button" className="button" disabled={pending} onClick={onCancel}>
          {t("common.cancel")}
        </button>
        <button type="button" className="button primary" disabled={blocked || pending} onClick={onConfirm}>
          {pending ? t("conversations.working") : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
