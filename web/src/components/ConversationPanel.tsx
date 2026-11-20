import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";
import { useTranslation } from "react-i18next";
import {
  conversationErrorText,
  conversationRecipientLabel,
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
import { timeText } from "../workbench/text";
import { Chevron, SendArrow, Spin } from "./Icons";
import { ModelConnections } from "./ModelConnections";
import "../styles/conversation-composer.css";

export function ConversationPanel({
  session,
  thread,
  onRemove,
  header,
}: {
  session: string;
  thread: ConversationSummary;
  onRemove: () => void;
  header?: (removeControl: ReactNode) => ReactNode;
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
      description: null,
    })),
    ...(models.data?.connections ?? []).filter(dispatchableConnection).map((connection) => {
      const choice = connectionChoiceLabel(t, connection);
      const model = connectionModelLabel(t, connection);
      const label = choice.endsWith(" · " + model) ? choice : choice + " · " + model;
      const provider = t(`team.providers.${connection.provider}`, { defaultValue: connection.provider });
      const description =
        connection.label && ![provider, connection.model].includes(connection.label) ? connection.label : null;
      return { ...connection, label, description, local: false };
    }),
  ];
  const [selected, setSelected] = useState("");
  const [text, setText] = useState("");
  const [pending, setPending] = useState<"preview" | "send" | "archive" | null>(null);
  const [error, setError] = useState("");
  const [removing, setRemoving] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [managingModels, setManagingModels] = useState(false);
  const modelTrigger = useRef<HTMLButtonElement>(null);
  const [prepared, setPrepared] = useState<{ scope: string; preview: ConversationPreview } | null>(null);
  const request = useRef<ConversationRequest | null>(null);
  const busy = useRef(false);
  const currentScope = useRef<string | null>(null);
  const formId = useId();
  const feed = useRef<HTMLOListElement>(null);
  const nearEnd = useRef(true);
  const messages = detail.data?.thread.messages ?? [];
  const running = detail.data?.thread.running ?? thread.running;
  const connection = options.find((option) => option.id === selected);
  const disabled = Boolean(pending || running || removing);
  const canPrepare = Boolean(!disabled && !detail.isError && detail.data && connection && text.trim());
  const scope = JSON.stringify([thread.id, detail.data?.thread.revision, connection?.id, connection?.fingerprint]);
  const details = prepared?.scope === scope ? prepared.preview : null;
  const hasHistory = messages.some((message) => message.role === "assistant");
  const context = [
    thread.node_id && t("conversations.includesNode"),
    hasHistory && t("conversations.includesRecent"),
  ].filter(Boolean);
  const destination = connection && t(connection.local ? "conversations.onDevice" : "conversations.external");

  useLayoutEffect(() => {
    currentScope.current = scope;
    return () => {
      currentScope.current = null;
    };
  }, [scope]);

  useLayoutEffect(() => {
    if (feed.current && nearEnd.current) feed.current.scrollTop = feed.current.scrollHeight;
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

  const recipient = (provider: string, model: string) => conversationRecipientLabel(t, provider, model);

  function speaker(message: ConversationMessage): string {
    if (message.role === "user") return t("conversations.you");
    if (message.provider && message.model) return recipient(message.provider, message.model);
    if (message.provider && message.requested_model) {
      return t("conversations.requestedModel", { name: recipient(message.provider, message.requested_model) });
    }
    return t("model.fallback");
  }
  const remove = (
    <button type="button" className="conversation-text-action" disabled={disabled} onClick={() => setRemoving(true)}>
      {t("conversations.remove")}
    </button>
  );
  return (
    <section className="conversation-panel" aria-label={thread.title}>
      {header?.(remove)}
      {detail.isPending ? (
        <p role="status">{t("common.loading")}</p>
      ) : detail.isError ? (
        <p role="alert">{conversationErrorText(t, detail.error)}</p>
      ) : (
        <ol
          className="chat-feed"
          ref={feed}
          aria-label={t("chat.feed")}
          tabIndex={0}
          onScroll={({ currentTarget }) => {
            nearEnd.current = currentTarget.scrollHeight - currentTarget.scrollTop - currentTarget.clientHeight < 72;
          }}
        >
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
          <ComposerMenu
            kind="context"
            label={t("conversations.contextMenu")}
            trigger={<ContextIcon />}
            disabled={removing}
          >
            <p className="composer-context-title" aria-hidden="true">
              {t("conversations.contextMenu")}
            </p>
            <p className="composer-context-summary">{context.join(" · ") || t("conversations.messageOnly")}</p>
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className="composer-menu-item"
              onClick={() => {
                setShowDetails(!showDetails);
                if (!showDetails && !details) void prepare(false);
              }}
            >
              {t(showDetails ? "conversations.hideRequestDetails" : "conversations.requestDetails")}
            </button>
          </ComposerMenu>
          <ComposerMenu
            kind="model"
            triggerRef={modelTrigger}
            label={t("conversations.model")}
            tooltip={connection?.label ?? t("conversations.chooseModel")}
            valueId={connection ? formId + "-model-label" : undefined}
            trigger={
              <>
                <span id={formId + "-model-label"} className="composer-model-label">
                  {connection?.label ?? t("conversations.chooseModel")}
                </span>
                <Chevron />
              </>
            }
            disabled={disabled}
          >
            {[
              { label: t("conversations.onDevice"), matches: options.filter((model) => model.local) },
              { label: t("conversations.connectedModels"), matches: options.filter((model) => !model.local) },
            ].map(
              ({ label, matches }) =>
                matches.length > 0 && (
                  <div className="composer-model-group" key={label} role="group" aria-label={label}>
                    <p aria-hidden="true">{label}</p>
                    {matches.map((model) => (
                      <button
                        key={model.id}
                        type="button"
                        role="menuitemradio"
                        aria-checked={connection?.id === model.id}
                        tabIndex={-1}
                        className="composer-menu-item"
                        onClick={() => {
                          setSelected(model.id);
                          changeDraft();
                        }}
                      >
                        <span>{model.description ? recipient(model.provider, model.model) : model.label}</span>
                        {model.description && <small>{model.description}</small>}
                      </button>
                    ))}
                  </div>
                ),
            )}
            {models.isPending || local.isPending ? <p role="status">{t("common.loading")}</p> : null}
            <button
              type="button"
              className="composer-menu-item composer-model-settings"
              role="menuitem"
              tabIndex={-1}
              onClick={() => setManagingModels(true)}
            >
              {t(options.length ? "conversations.manageModels" : "conversations.connect")}
            </button>
          </ComposerMenu>
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
        <p id={formId + "-scope"}>{[destination, ...context].filter(Boolean).join(" · ")}</p>
        {!header && <div className="conversation-secondary">{remove}</div>}
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
      {managingModels && (
        <ModelConnections
          session={session}
          onClose={() => {
            setManagingModels(false);
            modelTrigger.current?.focus();
          }}
        />
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

function ComposerMenu({
  kind,
  label,
  tooltip,
  valueId,
  trigger,
  triggerRef,
  disabled,
  children,
}: {
  kind: "context" | "model";
  label: string;
  tooltip?: string;
  valueId?: string;
  trigger: ReactNode;
  triggerRef?: RefObject<HTMLButtonElement | null>;
  disabled: boolean;
  children: ReactNode;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const container = useRef<HTMLDivElement>(null);
  const ownTrigger = useRef<HTMLButtonElement>(null);
  const button = triggerRef ?? ownTrigger;
  const menu = useRef<HTMLDivElement>(null);
  const opening = useRef<"last" | "selected">("selected");
  const visible = open && !disabled;

  useLayoutEffect(() => {
    if (!visible) return;
    const items = menu.current?.querySelectorAll<HTMLElement>(".composer-menu-item");
    const selected = menu.current?.querySelector<HTMLElement>('[aria-checked="true"]');
    const initial = opening.current === "last" ? items?.[items.length - 1] : (selected ?? items?.[0]);
    initial?.focus();
    opening.current = "selected";
  }, [visible]);

  useEffect(() => {
    if (!open) return;
    if (disabled) {
      setOpen(false);
      return;
    }
    function outside(event: PointerEvent) {
      if (event.target instanceof Node && !container.current?.contains(event.target)) setOpen(false);
    }
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [open, disabled]);

  function dismiss() {
    setOpen(false);
    button.current?.focus();
  }

  function move(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      dismiss();
      return;
    }
    const items = Array.from(menu.current?.querySelectorAll<HTMLElement>(".composer-menu-item") ?? []);
    const index = items.findIndex((item) => item === document.activeElement);
    const next =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? items.length - 1
          : event.key === "ArrowDown"
            ? (index + 1) % items.length
            : event.key === "ArrowUp"
              ? (index - 1 + items.length) % items.length
              : -1;
    if (!items[next]) return;
    event.preventDefault();
    items[next].focus();
  }

  return (
    <div
      className="composer-menu"
      data-kind={kind}
      ref={container}
      onBlur={(event) => {
        if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false);
      }}
    >
      <button
        type="button"
        ref={button}
        className="composer-menu-trigger"
        aria-label={label}
        aria-describedby={valueId}
        title={tooltip ?? label}
        aria-haspopup="menu"
        aria-expanded={visible}
        aria-controls={id}
        disabled={disabled}
        onClick={() => setOpen(!visible)}
        onKeyDown={(event) => {
          if (["ArrowDown", "ArrowUp"].includes(event.key)) {
            event.preventDefault();
            opening.current = event.key === "ArrowUp" ? "last" : "selected";
            if (visible) {
              const items = menu.current?.querySelectorAll<HTMLElement>(".composer-menu-item");
              items?.[event.key === "ArrowUp" ? items.length - 1 : 0]?.focus();
            } else {
              setOpen(true);
            }
          }
        }}
      >
        {trigger}
      </button>
      <div
        id={id}
        ref={menu}
        hidden={!visible}
        role="menu"
        aria-label={label}
        className="composer-popover"
        onKeyDown={move}
        onClick={(event) => {
          if (event.target instanceof Element && event.target.closest(".composer-menu-item")) dismiss();
        }}
      >
        {children}
      </div>
    </div>
  );
}

function ContextIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3.5 8h9 M8 3.5v9" />
    </svg>
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
