import { useId, useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../api/client";
import {
  conversationErrorText,
  useConversationActions,
  type ConversationAnchor,
  type ConversationPreview,
  type ConversationRequest,
  type ConversationSummary,
} from "../api/conversations";
import type { TeamAgent } from "../api/teamAgents";
import { useComposerKeyboard } from "../hooks/useComposerKeyboard";
import { useConversationEditor } from "../shell/ConversationEditors";
import { href } from "../shell/route";
import { ConversationModelPicker, previewMatchesModel, useConversationModels } from "./ConversationModels";
import { ContextIcon } from "./ConversationPanel";
import { ConversationRecipient, ConversationStarters, DIRECT_MODEL } from "./ConversationRecipients";
import { Menu } from "./FloatingControls";
import { SendArrow, Spin } from "./Icons";
import { ModelConnections } from "./ModelConnections";
import "../styles/conversation-composer.css";

export type DraftAnchor = { anchor: ConversationAnchor; title: string };

export function NewConversation({
  session,
  agents,
  agentsPending,
  agentsError,
  initialRecipient,
  anchor,
  onDetach,
  onCreated,
  focusComposer,
}: {
  session: string;
  agents: TeamAgent[];
  agentsPending?: boolean;
  agentsError?: boolean;
  initialRecipient?: string;
  anchor?: DraftAnchor | null;
  onDetach?: () => void;
  onCreated: (thread: ConversationSummary) => void;
  focusComposer?: boolean;
}) {
  const { t } = useTranslation();
  const scope = anchor?.anchor;
  const draftId = JSON.stringify(["new", scope?.claim_id, scope?.node_id, scope?.statement_id, initialRecipient]);
  const { editor, update, updateThread, reserve, release, keepsRequest } = useConversationEditor(session, draftId);
  const { text, recipientId, selectedModel, pending, error } = editor;
  // A sole saved Agent has an unambiguous default. With several, keep the typed draft and let the user choose.
  const selected = recipientId ?? initialRecipient ?? (agents.length === 1 ? agents[0]?.id : undefined);
  const agent = agents.find(({ id }) => id === selected);
  const direct = selected === DIRECT_MODEL;
  const actions = useConversationActions(session);
  const models = useConversationModels(session, { connected: Boolean(agent || direct), discoverLocal: direct });
  const connection = models.options.find(({ id }) => id === (direct ? selectedModel : agent?.connection_id));
  const destination = connection && t(connection.local ? "conversations.onDevice" : "conversations.external");
  const context = anchor ? t("agentDock.anchor", { title: anchor.title }) : t("conversations.messageOnly");
  const label = agent ? t("employeeChats.messageEmployee", { name: agent.name }) : t("conversations.message");
  const contentId = useId();
  const input = useRef<HTMLTextAreaElement>(null);
  const recipientTrigger = useRef<HTMLButtonElement>(null);
  const modelTrigger = useRef<HTMLButtonElement>(null);
  const [managingModels, setManagingModels] = useState(false);
  const signedScope = JSON.stringify([draftId, selected, agent?.revision, connection?.id, connection?.fingerprint]);
  const currentScope = useRef<string | null>(null);
  useLayoutEffect(() => {
    currentScope.current = signedScope;
    return () => {
      currentScope.current = null;
    };
  }, [signedScope]);
  useLayoutEffect(() => {
    if (focusComposer) input.current?.focus();
    // Each explicit Ask an Agent action has a fresh anchor, even for the same node.
  }, [focusComposer, anchor]);
  const keyboard = useComposerKeyboard(() => {
    if (!recipientTrigger.current || recipientTrigger.current.disabled) return false;
    recipientTrigger.current.click();
    return true;
  });
  const canSend = Boolean((agent || direct) && connection && text.trim() && !pending);

  function change(change: { text?: string; recipientId?: string; selectedModel?: string }) {
    update((current) => ({ ...current, ...change, error: "", request: null }));
  }

  function detach() {
    const unanchored = JSON.stringify(["new", null, null, null, initialRecipient]);
    updateThread(unanchored, (current) =>
      current.text
        ? current
        : {
            ...current,
            text,
            recipientId,
            selectedModel,
          },
    );
    onDetach?.();
  }

  async function prepare(send: boolean) {
    if (!canSend || !connection || !reserve()) return;
    const textToSend = text.trim();
    update((current) => ({ ...current, pending: send ? "send" : "preview", error: "" }));
    let thread: ConversationSummary | undefined;
    let request: ConversationRequest | null = null;
    let reviewed: { scope: string; payload: ConversationPreview } | undefined;
    let message = "";
    let delivered = false;
    let retryCreate = true;
    try {
      const name = anchor?.title.slice(0, 200) || t("conversations.defaultTitle");
      const createScope = JSON.stringify([draftId, agent?.id, name]);
      const id = editor.creation?.scope === createScope ? editor.creation.id : crypto.randomUUID();
      update((current) => ({ ...current, creation: { scope: createScope, id } }));
      const created = await actions.create(name, scope ?? {}, agent?.id, id);
      retryCreate = false;
      if (
        (created.agent_id ?? null) !== (agent?.id ?? null) ||
        created.claim_id !== (scope?.claim_id ?? null) ||
        created.node_id !== (scope?.node_id ?? null) ||
        created.statement_id !== (scope?.statement_id ?? null)
      ) {
        throw new ApiError(409, "conversation.stale");
      }
      thread = created;
      request = {
        thread_id: thread.id,
        connection_id: connection.id,
        text: textToSend,
        request_id: crypto.randomUUID(),
      };
      if (currentScope.current !== signedScope && (!send || !keepsRequest())) return;
      const preview = await actions.preview(request);
      if (
        ((!send || !keepsRequest()) && currentScope.current === null) ||
        (currentScope.current !== null && currentScope.current !== signedScope) ||
        !previewMatchesModel(preview, connection)
      ) {
        throw new ApiError(409, "conversation.stale");
      }
      if (send) {
        await actions.send(request, preview.token, preview.external);
        delivered = true;
      } else {
        const threadScope = JSON.stringify([
          thread.id,
          thread.revision,
          thread.agent_id,
          agent?.revision,
          connection.id,
          connection.fingerprint,
        ]);
        reviewed = { scope: threadScope, payload: preview };
      }
    } catch (cause) {
      message = conversationErrorText(t, cause);
      if (cause instanceof ApiError && (cause.code ?? cause.message) === "conversation.stale") retryCreate = false;
    } finally {
      if (thread) {
        const retryRequest = request;
        const modelId = connection.id;
        updateThread(thread.id, (current) =>
          current.text
            ? current
            : {
                ...current,
                text: delivered ? "" : text,
                selectedModel: modelId,
                request: delivered ? null : retryRequest,
                error: message,
                preview: reviewed,
              },
        );
      }
      update((current) => ({
        ...current,
        pending: null,
        creation: retryCreate ? current.creation : undefined,
        text: thread && current.text === text ? "" : current.text,
        error: thread ? "" : message,
      }));
      release();
      if (thread && currentScope.current === signedScope) onCreated(thread);
    }
  }

  return (
    <section className="conversation-panel new-conversation" aria-label={t("agentDock.newConversation")}>
      <div className="new-conversation-body">
        {!text && !pending && (
          <ConversationStarters
            anchor={scope}
            onSelect={(value) => {
              change({ text: value });
              input.current?.focus();
            }}
          />
        )}
      </div>
      <form
        className="composer direct-composer"
        onSubmit={(event) => {
          event.preventDefault();
          void prepare(true);
        }}
      >
        <label className="sr-only" htmlFor={contentId}>
          {label}
        </label>
        <textarea
          {...keyboard}
          ref={input}
          id={contentId}
          value={text}
          rows={2}
          maxLength={1500}
          disabled={Boolean(pending)}
          placeholder={label}
          onChange={(event) => change({ text: event.currentTarget.value })}
        />
        <div className="composer-row">
          <Menu
            label={t("conversations.contextMenu")}
            trigger={<ContextIcon />}
            triggerClassName="composer-menu-trigger composer-context-trigger"
            placement="top-start"
            intro={<p className="composer-context-summary">{[destination, context].filter(Boolean).join(" · ")}</p>}
            groups={[
              {
                options: [
                  {
                    id: "request-details",
                    label: t("conversations.requestDetails"),
                    disabled: !canSend,
                    onSelect: () => void prepare(false),
                  },
                ],
              },
            ]}
          />
          <ConversationRecipient
            choice={{
              agents,
              pending: agentsPending,
              onSelect: (id) => {
                change({ recipientId: id });
                input.current?.focus();
              },
            }}
            selected={selected}
            disabled={Boolean(pending)}
            triggerRef={recipientTrigger}
            textarea={input}
          />
          {direct && (
            <ConversationModelPicker
              models={models}
              value={selectedModel}
              disabled={Boolean(pending)}
              textarea={input}
              triggerRef={modelTrigger}
              onSelect={(id) => change({ selectedModel: id })}
              onManage={() => setManagingModels(true)}
            />
          )}
          <button
            type="submit"
            className="send"
            disabled={!canSend}
            aria-label={t(pending === "send" ? "conversations.sending" : "conversations.send")}
            aria-describedby={contentId + "-scope"}
          >
            {pending ? <Spin /> : <SendArrow />}
          </button>
        </div>
        {agentsError && (
          <p className="composer-status" role="alert">
            {t("teamAgents.listFailed")}
          </p>
        )}
        {!agents.length && !agentsPending && !agentsError && !direct && (
          <p className="composer-status">
            {t("agentDock.addAgentHint")} <a href={href("team", "agents")}>{t("agentDock.addAgent")}</a>
          </p>
        )}
        {agent && !connection && !models.pending && (
          <p className="composer-status">{t("conversations.employeeConnectionUnavailable")}</p>
        )}
        {error && (
          <p className="composer-status" data-error="true" role="alert">
            {error}
          </p>
        )}
        {pending && (
          <p className="composer-status" role="status">
            {t(pending === "send" ? "conversations.sending" : "common.loading")}
          </p>
        )}
      </form>
      <div className="conversation-context">
        <p id={contentId + "-scope"}>{[destination, context].filter(Boolean).join(" · ")}</p>
        {anchor && onDetach && (
          <button className="conversation-text-action" type="button" disabled={Boolean(pending)} onClick={detach}>
            {t("agentDock.detach")}
          </button>
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
    </section>
  );
}
