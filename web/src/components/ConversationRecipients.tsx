import type { RefObject } from "react";
import { useTranslation } from "react-i18next";
import type { ConversationAnchor } from "../api/conversations";
import type { TeamAgent } from "../api/teamAgents";
import { href } from "../shell/route";
import { Picker } from "./FloatingControls";
import { Chevron } from "./Icons";
import { TeamAgentAvatar } from "./TeamAgentAvatar";

export const DIRECT_MODEL = "direct-model";

export type RecipientChoice = {
  agents: TeamAgent[];
  pending?: boolean;
  onSelect: (id: string) => void;
};

export function ConversationRecipient({
  choice,
  selected,
  disabled,
  triggerRef,
  textarea,
}: {
  choice: RecipientChoice;
  selected?: string;
  disabled: boolean;
  triggerRef: RefObject<HTMLButtonElement | null>;
  textarea: RefObject<HTMLTextAreaElement | null>;
}) {
  const { t } = useTranslation();
  const agent = choice.agents.find(({ id }) => id === selected);
  return (
    <Picker
      label={t("agentDock.chooseAgent")}
      searchLabel={t("agentDock.searchAgents")}
      emptyLabel={t(choice.pending ? "common.loading" : "agentDock.noAgents")}
      triggerClassName="composer-menu-trigger composer-recipient-trigger"
      triggerRef={triggerRef}
      placement="top-start"
      keepVisible={textarea}
      value={selected}
      disabled={disabled}
      onSelect={choice.onSelect}
      focusOnSelect={false}
      trigger={
        <>
          {agent && <TeamAgentAvatar avatar={agent.avatar} name={agent.name} />}
          <span>{agent?.name ?? t(selected === DIRECT_MODEL ? "agentDock.directModel" : "agentDock.chooseAgent")}</span>
          <Chevron />
        </>
      }
      groups={[
        {
          options: choice.agents.map((item) => ({
            id: item.id,
            label: item.name,
            detail: item.description,
            icon: <TeamAgentAvatar avatar={item.avatar} name={item.name} />,
          })),
        },
        { options: [{ id: DIRECT_MODEL, label: t("agentDock.directModel") }] },
      ]}
      footer={
        !choice.agents.length && !choice.pending ? (
          <a className="conversation-text-action" href={href("team", "agents")}>
            {t("agentDock.addAgent")}
          </a>
        ) : undefined
      }
    />
  );
}

export function ConversationStarters({
  anchor,
  onSelect,
}: {
  anchor?: ConversationAnchor;
  onSelect: (text: string) => void;
}) {
  const { t } = useTranslation();
  const level = anchor?.node_id ? "node" : anchor?.claim_id ? "claim" : "project";
  return (
    <div className="conversation-starters" role="group" aria-label={t("agentDock.starters")}>
      {["explain", "next"].map((suggestion) => {
        const text = t(`agentDock.prompts.${level}.${suggestion}`);
        return (
          <button type="button" className="conversation-starter" key={suggestion} onClick={() => onSelect(text)}>
            {text}
          </button>
        );
      })}
    </div>
  );
}
