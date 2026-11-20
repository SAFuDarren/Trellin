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
import { SendArrow, Spin } from "./Icons";

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
      provider: "ollama-native",
      model: name + ":local",
      fingerprint: null,
      local: true,
    })),
    ...(models.data?.connections ?? []).filter(dispatchableConnection).map((connection) => {
      const choice = connectionChoiceLabel(t, connection);
      const model = connectionModelLabel(t, connection);
      const label = choice.endsWith(" · " + model) ? choice : choice + " · " + model;
      return { ...connection, label, local: false };
    }),
  ];
  const [selected, setSelected] = useState("");
  const [text, setText] = useState("");
  const [pending, setPending] = useState<"preview" | "send" | "archive" | null>(null);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [prepared, setPrepared] = useState<{ scope: string; preview: ConversationPreview } | null>(null);
  const request = useRef<ConversationRequest | null>(null);
  const busy = useRef(false);
  const currentScope = useRef<string | null>(null);
  const formId = useId();
  const feed = useRef<HTMLOListElement>(null);
  const messages = detail.data?.thread.messages ?? [];
  const running = detail.data?.thread.running ?? thread.running;
  const connection = options.find((option) => option.id === selected);
  const disabled = Boolean(pending || running || removing);
  const canPrepare = Boolean(!disabled && !detail.isError && detail.data && connection && text.trim());
  const scope = JSON.stringify([thread.id, detail.data?.thread.revision, connection?.id, connection?.fingerprint]);
  const details = prepared?.scope === scope ? prepared.preview : null;
  const hasHistory = messages.some((message) => message.role === "assistant");

  useLayoutEffect(() => {
    currentScope.current = scope;
    return () => {
      currentScope.current = null;
    };
  }, [scope]);

  useLayoutEffect(() => {
    if (feed.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [messages.length]);

  function errorText(code: string) {
    return t(`errors.${code}`, { defaultValue: t("conversations.failed") });
  }

  function changeDraft() {
    request.current = null;
    setPrepared(null);
    setError("");
  }

  async function prepare(send: boolean) {
    if (!canPrepare || !connection || busy.current) return;
    if (send && prepared && !details) {
      setPrepared(null);
      setError(t("errors.conversation.stale"));
      return;
    }
    const textToSend = text.trim();
    const previous = request.current;
    const body =
      previous?.text === textToSend && previous.connection_id === connection.id
        ? previous
        : { thread_id: thread.id, connection_id: connection.id, text: textToSend, request_id: crypto.randomUUID() };
    request.current = body;
    busy.current = true;
    setError("");
    setPending(send ? "send" : "preview");
    try {
      const preview = details ?? (await actions.preview(body));
      if (currentScope.current === null) return;
      if (
        currentScope.current !== scope ||
        preview.external !== !connection.local ||
        preview.provider !== connection.provider ||
        preview.model !== connection.model
      ) {
        setPrepared(null);
        setError(t("errors.conversation.stale"));
        return;
      }
      if (send) {
        await actions.send(body, preview.token, preview.external);
        setText("");
        changeDraft();
        setShowDetails(false);
      } else {
        setPrepared({ scope, preview });
      }
    } catch (cause) {
      setPrepared(null);
      setError(conversationErrorText(t, cause));
    } finally {
      busy.current = false;
      setPending(null);
    }
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void prepare(true);
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
      <form className="composer direct-composer" onSubmit={send}>
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
            changeDraft();
          }}
        />
        <div className="composer-row">
          <label className="sr-only" htmlFor={formId + "-model"}>
            {t("conversations.model")}
          </label>
          <span className="conversation-model-picker">
            <select
              id={formId + "-model"}
              value={connection?.id ?? ""}
              disabled={disabled || !options.length}
              onChange={(event) => {
                setSelected(event.target.value);
                changeDraft();
              }}
            >
              <option value="">{t("conversations.chooseModel")}</option>
              {options.map((option) => (
                <option key={option.id} value={option.id}>
                  {option.label}
                </option>
              ))}
            </select>
          </span>
          {!models.isPending && !local.isPending && !options.length && (
            <a href={href("team", "runtimes")}>{t("conversations.connect")}</a>
          )}
          <button
            type="submit"
            className="send"
            disabled={!canPrepare}
            aria-label={t(pending === "send" || running ? "conversations.working" : "conversations.send")}
            title={t("conversations.send")}
            aria-describedby={formId + "-scope"}
          >
            {pending === "send" || running ? <Spin /> : <SendArrow />}
          </button>
        </div>
        {models.isError && <p role="alert">{t("modelConnections.failed")}</p>}
        {(error || detail.data?.thread.latest_error) && (
          <p className="composer-status" data-error="true" role="alert">
            {error || errorText(detail.data?.thread.latest_error ?? "")}
          </p>
        )}
      </form>
      <div className="conversation-context">
        <p id={formId + "-scope"}>
          {[
            connection && t(connection.local ? "conversations.onDevice" : "conversations.external"),
            thread.node_id && t("conversations.includesNode"),
            hasHistory && t("conversations.includesRecent"),
          ]
            .filter(Boolean)
            .join(" · ")}
        </p>
        <div className="conversation-secondary">
          <button
            type="button"
            className="conversation-text-action"
            aria-expanded={showDetails}
            aria-controls={formId + "-request"}
            onClick={() => {
              setShowDetails(!showDetails);
              if (!showDetails && !details) void prepare(false);
            }}
          >
            {t("conversations.requestDetails")}
          </button>
          <button
            type="button"
            className="conversation-text-action"
            disabled={disabled}
            onClick={() => setRemoving(true)}
          >
            {t("conversations.remove")}
          </button>
        </div>
      </div>
      <div className="conversation-request" id={formId + "-request"} hidden={!showDetails}>
        {details ? (
          <pre tabIndex={0} aria-label={t("conversations.requestDetails")}>
            {details.outgoing}
          </pre>
        ) : pending === "preview" ? (
          <p role="status">{t("common.loading")}</p>
        ) : canPrepare ? (
          <button type="button" className="conversation-text-action" onClick={() => void prepare(false)}>
            {t("conversations.previewRequest")}
          </button>
        ) : (
          <p>{t("conversations.previewHint")}</p>
        )}
      </div>
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
