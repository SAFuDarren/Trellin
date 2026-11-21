import type { Ref, RefObject } from "react";
import { useTranslation } from "react-i18next";
import { conversationRecipientLabel, type ConversationPreview } from "../api/conversations";
import { useLocalTextModels } from "../api/localModels";
import {
  connectionChoiceLabel,
  connectionModelLabel,
  dispatchableConnection,
  useModelConnections,
} from "../api/modelConnections";
import { harnessName } from "../workbench/text";
import { Picker } from "./FloatingControls";
import { Chevron } from "./Icons";
import { TeamAgentAvatar } from "./TeamAgentAvatar";

export function useConversationModels(session: string, { connected = true, discoverLocal = true } = {}) {
  const { t } = useTranslation();
  const models = useModelConnections(session, connected);
  const local = useLocalTextModels(session, discoverLocal);
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
  return {
    options,
    pending: (connected && models.isPending) || (discoverLocal && local.isPending),
    error: connected && models.isError,
  };
}

type Models = ReturnType<typeof useConversationModels>;

export function previewMatchesModel(preview: ConversationPreview, model: Models["options"][number]) {
  return preview.external === !model.local && preview.provider === model.provider && preview.model === model.model;
}

export function ConversationModelPicker({
  models,
  value,
  disabled,
  textarea,
  triggerRef,
  onSelect,
  onManage,
}: {
  models: Models;
  value: string;
  disabled: boolean;
  textarea: RefObject<HTMLTextAreaElement | null>;
  triggerRef: Ref<HTMLButtonElement>;
  onSelect: (id: string) => void;
  onManage: () => void;
}) {
  const { t } = useTranslation();
  const connection = models.options.find((model) => model.id === value);
  const { options } = models;
  return (
    <Picker
      label={t("conversations.model")}
      triggerRef={triggerRef}
      triggerClassName="composer-menu-trigger composer-model-trigger"
      placement="top-end"
      keepVisible={textarea}
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
          label: model.description ? conversationRecipientLabel(t, model.provider, model.model) : model.label,
          detail: model.description ?? undefined,
          icon: <TeamAgentAvatar avatar="initials" name={harnessName(model.provider)} />,
        })),
      }))}
      searchLabel={t("conversations.searchModels")}
      emptyLabel={t(models.pending ? "common.loading" : "picker.noMatches")}
      onSelect={onSelect}
      footer={(dismiss) => (
        <button
          type="button"
          className="composer-model-settings"
          onClick={() => {
            dismiss();
            onManage();
          }}
        >
          {t(options.length ? "conversations.manageModels" : "conversations.connect")}
        </button>
      )}
    />
  );
}
