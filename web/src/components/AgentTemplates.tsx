import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import type { TeamAgent } from "../api/teamAgents";
import { TeamAgentAvatar } from "./TeamAgentAvatar";

/** #323: role templates that fill every Agent field, so a researcher reviews instead of writing from scratch. */
export const AGENT_TEMPLATES = [
  { id: "prover", avatar: "spark" },
  { id: "literature", avatar: "book" },
  { id: "reviewer", avatar: "compass" },
  { id: "planner", avatar: "initials" },
] as const satisfies readonly { id: string; avatar: TeamAgent["avatar"] }[];

export type AgentTemplateId = (typeof AGENT_TEMPLATES)[number]["id"];

/** A new, unsaved Agent filled from a template, or blank. The connection is chosen by the wizard. */
export function agentDraft(t: TFunction, template?: AgentTemplateId): TeamAgent {
  const chosen = AGENT_TEMPLATES.find(({ id }) => id === template);
  const text = (field: string) => (chosen ? t(`agentTemplates.roles.${chosen.id}.${field}`) : "");
  return {
    id: crypto.randomUUID(),
    revision: 0,
    connection_id: "",
    name: text("name"),
    description: text("description"),
    avatar: chosen?.avatar ?? "initials",
    instructions: text("instructions"),
    starters: chosen ? [text("starter1"), text("starter2")] : [],
  };
}

/** The choice of roles, shown in the Add agent wizard and in an empty Chats panel. */
export function AgentTemplateList({
  onChoose,
  compact = false,
}: {
  onChoose: (template: AgentTemplateId) => void;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <ul
      className="agent-template-list"
      data-compact={compact || undefined}
      aria-label={t("agentTemplates.roles.label")}
    >
      {AGENT_TEMPLATES.map(({ id, avatar }) => {
        const name = t(`agentTemplates.roles.${id}.name`);
        return (
          <li key={id}>
            <button type="button" className="agent-template" onClick={() => onChoose(id)}>
              <TeamAgentAvatar avatar={avatar} name={name} />
              <span className="agent-template-text">
                <strong>{name}</strong>
                <span>{t(`agentTemplates.roles.${id}.description`)}</span>
              </span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
