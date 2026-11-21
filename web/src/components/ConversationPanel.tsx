import { useEffect, useId, useLayoutEffect, useRef, useState, type FormEvent, type ReactNode, type Ref } from "react";
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
import { useTeamAgents, type TeamAgent } from "../api/teamAgents";
import { useComposerKeyboard } from "../hooks/useComposerKeyboard";
import { useConversationEditor } from "../shell/ConversationEditors";
import { harnessName, timeText } from "../workbench/text";
import { Chevron, SendArrow, Spin } from "./Icons";
import { Menu, Picker } from "./FloatingControls";
import { ModelConnections } from "./ModelConnections";
import { TeamAgentAvatar } from "./TeamAgentAvatar";
import "../styles/conversation-composer.css";

export function ConversationPanel({
  session,
  thread,
  onRemove,
  header,
  employee,
  focusComposer = false,
}: {
  session: string;
  thread: ConversationSummary;
  onRemove: () => void;
  header?: (actions: { remove: () => void; removeDisabled: boolean; triggerRef: Ref<HTMLButtonElement> }) => ReactNode;
  employee?: Pick<TeamAgent, "id" | "name" | "connection_id" | "revision">;
  focusComposer?: boolean;
}) {
  const { t, i18n } = useTranslation();
  const detail = useConversation(session, thread.id);
  const employeeId = detail.data?.thread.agent_id ?? thread.agent_id;
  const profiles = useTeamAgents(session, Boolean(employeeId && !employee));
  const agent =
    employee?.id === employeeId ? employee : profiles.data?.agents.find((profile) => profile.id === employeeId);
  const employeeScoped = Boolean(employeeId);
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
  const { editor, update, reserve, release, keepsRequest } = useConversationEditor(session, thread.id);
  const { text, selectedModel, pending, error } = editor;
  const [removing, setRemoving] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  const [managingModels, setManagingModels] = useState(false);
  const modelTrigger = useRef<HTMLButtonElement>(null);
  const archiveTrigger = useRef<HTMLButtonElement>(null);
  const restoreArchiveFocus = useRef(false);
  const [prepared, setPrepared] = useState<{ scope: string; preview: ConversationPreview } | null>(null);
  const currentScope = useRef<string | null>(null);
  const composerKeyboard = useComposerKeyboard();
  const textarea = useRef<HTMLTextAreaElement>(null);
  const initialFocus = useRef(focusComposer);
  const formId = useId();
  const feed = useRef<HTMLOListElement>(null);
  const nearEnd = useRef(true);
  const messages = detail.data?.thread.messages ?? [];
  const running = detail.data?.thread.running ?? thread.running;
  const sending = pending === "send";
  const latestError = detail.data?.thread.latest_error;
  const latestMessage = messages[messages.length - 1];
  const connection = options.find((option) => option.id === (employeeScoped ? agent?.connection_id : selectedModel));
  const startedAt = running && latestMessage?.role === "user" ? latestMessage.at : undefined;
  const messageLabel = agent ? t("employeeChats.messageEmployee", { name: agent.name }) : t("conversations.message");
  const waiting = agent ? t("conversations.awaitingEmployee", { name: agent.name }) : t("conversations.awaitingReply");
  const disabled = Boolean(pending || running || removing);
  const canPrepare = Boolean(!disabled && !detail.isError && detail.data && connection && text.trim());
  const scope = JSON.stringify([
    thread.id,
    detail.data?.thread.revision,
    employeeId,
    agent?.revision,
    connection?.id,
    connection?.fingerprint,
  ]);
  const details = prepared?.scope === scope ? prepared.preview : null;
  const hasHistory = messages.some((message) => message.role === "assistant");
  const context = [
    thread.node_id && t("conversations.includesNode"),
    hasHistory && t("conversations.includesRecent"),
  ].filter(Boolean);
  const taskScope = employeeScoped && t("employeeChats.taskScope", { title: thread.title });
  const destination = connection && t(connection.local ? "conversations.onDevice" : "conversations.external");

  useLayoutEffect(() => {
    currentScope.current = scope;
    return () => {
      currentScope.current = null;
    };
  }, [scope]);

  useLayoutEffect(() => {
    if (feed.current && nearEnd.current) feed.current.scrollTop = feed.current.scrollHeight;
  }, [latestMessage?.id, running, sending]);

  useLayoutEffect(() => {
    if (removing || !restoreArchiveFocus.current) return;
    restoreArchiveFocus.current = false;
    archiveTrigger.current?.focus({ preventScroll: true });
  }, [removing]);

  useEffect(() => {
    if (initialFocus.current && detail.data && !disabled) {
      initialFocus.current = false;
      textarea.current?.focus();
    }
  }, [detail.data, disabled]);

  function errorText(code: string) {
    return t(`errors.${code}`, { defaultValue: t("conversations.failed") });
  }

  function changeDraft(change: { text?: string; selectedModel?: string }) {
    update((state) => ({ ...state, ...change, request: null, error: "" }));
    setPrepared(null);
  }

  async function prepare(send: boolean) {
    if (!canPrepare || !connection) return;
    if (send && prepared && !details) {
      setPrepared(null);
      update((state) => ({ ...state, error: t("errors.conversation.stale") }));
      return;
    }
    if (!reserve()) return;
    const textToSend = text.trim();
    const previous = editor.request;
    const body: ConversationRequest =
      previous?.text === textToSend && previous.connection_id === connection.id
        ? previous
        : {
            thread_id: thread.id,
            connection_id: connection.id,
            text: textToSend,
            request_id: crypto.randomUUID(),
          };
    if (send) nearEnd.current = true;
    update((state) => ({ ...state, request: body, error: "", pending: send ? "send" : "preview" }));
    try {
      const preview = details ?? (await actions.preview(body));
      // Once Send was requested, switching panels can retire this editor while the workspace still owns the request.
      // The server checks the signed recipient, thread revision and outgoing context again before recording the turn.
      if (currentScope.current === null && !(send && keepsRequest())) return;
      if (
        (currentScope.current !== null && currentScope.current !== scope) ||
        preview.external !== !connection.local ||
        preview.provider !== connection.provider ||
        preview.model !== connection.model
      ) {
        setPrepared(null);
        update((state) => ({ ...state, error: t("errors.conversation.stale") }));
        return;
      }
      if (send) {
        await actions.send(body, preview.token, preview.external);
        update((state) => ({ ...state, text: state.text.trim() === textToSend ? "" : state.text, request: null }));
        setPrepared(null);
        setShowDetails(false);
      } else {
        setPrepared({ scope, preview });
      }
    } catch (cause) {
      setPrepared(null);
      update((state) => ({ ...state, error: conversationErrorText(t, cause) }));
    } finally {
      release();
      update((state) => ({ ...state, pending: null }));
    }
  }

  function send(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void prepare(true);
  }

  async function archive() {
    if (running || !reserve()) return;
    update((state) => ({ ...state, pending: "archive", error: "" }));
    try {
      await actions.archive(thread.id);
      update((state) => ({ ...state, text: "", request: null }));
      if (currentScope.current !== null) onRemove();
    } catch (cause) {
      update((state) => ({ ...state, error: conversationErrorText(t, cause) }));
    } finally {
      release();
      setRemoving(false);
      update((state) => ({ ...state, pending: null }));
    }
  }

  const recipient = (provider: string, model: string) => conversationRecipientLabel(t, provider, model);

  function modelSpeaker(message: ConversationMessage): string {
    if (message.provider && message.model) return recipient(message.provider, message.model);
    if (message.provider && message.requested_model) {
      return t("conversations.requestedModel", { name: recipient(message.provider, message.requested_model) });
    }
    return t("model.fallback");
  }
  const remove = (
    <button
      ref={archiveTrigger}
      type="button"
      className="conversation-text-action"
      disabled={disabled}
      onClick={() => setRemoving(true)}
    >
      {t("conversations.remove")}
    </button>
  );
  return (
    <section className="conversation-panel" aria-label={thread.title}>
      {header?.({ remove: () => setRemoving(true), removeDisabled: disabled, triggerRef: archiveTrigger })}
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
          {messages.length === 0 && !sending && !running && (
            <li className="chat-empty">{t("conversations.emptyThread")}</li>
          )}
          {messages.map((message) => (
            <li key={message.id} data-from={message.role === "user" ? "you" : "model"}>
              <span className="speaker">
                <b>
                  {message.role === "user" ? (
                    t("conversations.you")
                  ) : agent ? (
                    <span title={modelSpeaker(message)}>{agent.name}</span>
                  ) : (
                    modelSpeaker(message)
                  )}
                </b>
                {message.at && <time dateTime={message.at}>{timeText(i18n.language, message.at)}</time>}
              </span>
              <p className={message.role === "user" ? "bubble" : "message"}>{message.text}</p>
            </li>
          ))}
          {(sending || running) && (
            <li className="chat-send-status">
              {employeeScoped && <Spin />}
              <span role="status">{running ? waiting : t("conversations.sending")}</span>
              {employeeScoped && startedAt && <ReplyElapsed start={startedAt} locale={i18n.language} />}
            </li>
          )}
        </ol>
      )}
      <form className="composer direct-composer" onSubmit={send}>
        <label className="sr-only" htmlFor={formId + "-text"}>
          {messageLabel}
        </label>
        <textarea
          {...composerKeyboard}
          ref={textarea}
          id={formId + "-text"}
          value={text}
          rows={2}
          maxLength={1500}
          disabled={disabled || !detail.data}
          placeholder={messageLabel}
          onChange={(event) => changeDraft({ text: event.target.value })}
        />
        <div className="composer-row">
          <Menu
            label={t("conversations.contextMenu")}
            trigger={<ContextIcon />}
            triggerClassName="composer-menu-trigger composer-context-trigger"
            placement="top-start"
            disabled={removing}
            intro={<p className="composer-context-summary">{context.join(" · ") || t("conversations.messageOnly")}</p>}
            groups={[
              {
                options: [
                  {
                    id: "request-details",
                    label: t(showDetails ? "conversations.hideRequestDetails" : "conversations.requestDetails"),
                    onSelect: () => {
                      setShowDetails(!showDetails);
                      if (!showDetails && !details) void prepare(false);
                    },
                  },
                ],
              },
            ]}
          />
          {employeeScoped ? (
            connection && (
              <span className="composer-fixed-recipient" title={connection.label}>
                {connection.label}
              </span>
            )
          ) : (
            <Picker
              label={t("conversations.model")}
              triggerRef={modelTrigger}
              triggerClassName="composer-menu-trigger composer-model-trigger"
              placement="top-end"
              value={connection?.id}
              trigger={
                <>
                  <span className="composer-model-label">{connection?.label ?? t("conversations.chooseModel")}</span>
                  <Chevron />
                </>
              }
              disabled={disabled}
              groups={[
                { label: t("conversations.onDevice"), matches: options.filter((model) => model.local) },
                { label: t("conversations.connectedModels"), matches: options.filter((model) => !model.local) },
              ].map(({ label, matches }) => ({
                label,
                options: matches.map((model) => ({
                  id: model.id,
                  label: model.description ? recipient(model.provider, model.model) : model.label,
                  detail: model.description ?? undefined,
                  icon: <TeamAgentAvatar avatar="initials" name={harnessName(model.provider)} />,
                })),
              }))}
              searchLabel={t("conversations.searchModels")}
              emptyLabel={t(models.isPending || local.isPending ? "common.loading" : "picker.noMatches")}
              onSelect={(selectedModel) => {
                if (selectedModel !== editor.selectedModel) changeDraft({ selectedModel });
              }}
              footer={(dismiss) => (
                <button
                  type="button"
                  className="composer-model-settings"
                  onClick={() => {
                    dismiss();
                    setManagingModels(true);
                  }}
                >
                  {t(options.length ? "conversations.manageModels" : "conversations.connect")}
                </button>
              )}
            />
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
        {employeeScoped && !connection && !models.isPending && !(profiles.isPending && !employee) && (
          <p className="composer-status" role="status">
            {t("conversations.employeeConnectionUnavailable")}
          </p>
        )}
        {!sending && (error || latestError) && (
          <p className="composer-status" data-error="true" role="alert">
            {error || errorText(latestError ?? "")}
          </p>
        )}
        {!disabled && !error && latestError && !text && latestMessage?.role === "user" && (
          <button
            type="button"
            className="conversation-text-action chat-reuse-draft"
            onClick={() => {
              changeDraft({ text: latestMessage.text });
              textarea.current?.focus();
            }}
          >
            {t("conversations.reuseDraft")}
          </button>
        )}
      </form>
      <div className="conversation-context">
        <p id={formId + "-scope"}>{[taskScope, destination, ...context].filter(Boolean).join(" · ")}</p>
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
          <p>{t(employeeScoped ? "conversations.employeePreviewHint" : "conversations.previewHint")}</p>
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
          onCancel={() => {
            restoreArchiveFocus.current = true;
            setRemoving(false);
          }}
          onConfirm={() => void archive()}
        >
          <p>{t("conversations.removeHint")}</p>
        </Confirmation>
      )}
    </section>
  );
}

function ReplyElapsed({ start, locale }: { start: string; locale: string }) {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - Date.parse(start)) / 1000));
  if (!Number.isFinite(seconds)) return null;
  const elapsed = seconds >= 60 ? Math.floor(seconds / 60) : seconds;
  return (
    <span className="chat-send-elapsed" aria-hidden="true">
      {new Intl.NumberFormat(locale, {
        style: "unit",
        unit: seconds >= 60 ? "minute" : "second",
        unitDisplay: "short",
      }).format(elapsed)}
    </span>
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
  function dismiss() {
    ref.current?.close();
    onCancel();
  }
  return (
    <dialog
      ref={ref}
      className="conversation-confirmation"
      aria-labelledby={titleId}
      onCancel={(event) => {
        event.preventDefault();
        if (!pending) dismiss();
      }}
    >
      <h3 id={titleId}>{title}</h3>
      {children}
      <div className="choices">
        <button type="button" className="button" disabled={pending} onClick={dismiss}>
          {t("common.cancel")}
        </button>
        <button type="button" className="button primary" disabled={blocked || pending} onClick={onConfirm}>
          {pending ? t("conversations.working") : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
