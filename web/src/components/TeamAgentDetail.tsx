import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useAgentWork } from "../api/agentWork";
import { useConversations } from "../api/conversations";
import { connectionChoiceLabel, useModelConnections } from "../api/modelConnections";
import { useTeamAgents, type TeamAgent } from "../api/teamAgents";
import { EMPLOYEE_CHAT_PATH, href } from "../shell/route";
import { buildWorkspaceActivity, type ActivityState } from "../shell/workspaceActivity";
import { Empty, PageHead } from "./SectionLayout";
import { TeamAgentAvatar } from "./TeamAgentAvatar";
import { runtimeStatus, TeamAgentWizard } from "./TeamAgentProfiles";
import { WorkspaceActivityList } from "./WorkspaceActivity";

export function TeamAgentDetail({
  session,
  id,
  onOpenProof,
}: {
  session: string;
  id: string;
  onOpenProof: (claimId: string, nodeId?: string) => void;
}) {
  const { t } = useTranslation();
  const profiles = useTeamAgents(session);
  const agent = profiles.data?.agents.find((entry) => entry.id === id);
  if (!agent)
    return (
      <>
        <PageHead title={t("team.pages.agents")} />
        <Empty>
          {t(
            profiles.isPending
              ? "common.loading"
              : profiles.isError
                ? "teamAgents.listFailed"
                : "teamAgents.profileMissing",
          )}
        </Empty>
      </>
    );
  return <AgentProfile session={session} agent={agent} onOpenProof={onOpenProof} />;
}

function AgentProfile({
  session,
  agent,
  onOpenProof,
}: {
  session: string;
  agent: TeamAgent;
  onOpenProof: (claimId: string, nodeId?: string) => void;
}) {
  const { t } = useTranslation();
  const work = useAgentWork(session);
  const chats = useConversations(session);
  const connections = useModelConnections(session);
  const editTrigger = useRef<HTMLButtonElement>(null);
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState("");
  const [filter, setFilter] = useState<ActivityState | "all">("all");
  const tasks = work.data?.tasks.filter((task) => task.agent_id === agent.id);
  const threads = chats.data?.threads.filter((thread) => thread.agent_id === agent.id);
  const activity = buildWorkspaceActivity({ tasks, threads });
  const connection = connections.data?.connections.find((entry) => entry.id === agent.connection_id);
  const connectionStatus = connections.isPending
    ? t("common.loading")
    : connections.isError
      ? t("teamAgents.runtimesFailed")
      : runtimeStatus(t, connection);
  return (
    <>
      <PageHead
        title={agent.name}
        actions={
          <>
            <a className="button" href={href("chat", threads?.[0]?.id ?? EMPLOYEE_CHAT_PATH + agent.id)}>
              {t("teamAgents.openChat")}
            </a>
            {work.data?.available && (
              <a className="button primary" href={href("agent-work", "new/agent/" + agent.id)}>
                {t("agentWork.assign")}
              </a>
            )}
          </>
        }
      />
      <div className="page-body team-agent-detail">
        <div className="team-agent-detail-body">
          {agent.description && (
            <div className="team-agent-identity">
              <TeamAgentAvatar avatar={agent.avatar} name={agent.name} />
              <p>{agent.description}</p>
            </div>
          )}
          <section aria-labelledby="agent-detail-activity">
            <h3 id="agent-detail-activity">{t("teamAgents.workActivity")}</h3>
            <WorkspaceActivityList
              activity={activity}
              agents={new Map([[agent.id, agent]])}
              selectedFilter={filter}
              onFilter={setFilter}
              loading={work.isPending || chats.isPending}
              unavailable={work.isError || chats.isError}
              onOpenProof={onOpenProof}
              onOpenThread={(thread) => (window.location.hash = href("chat", thread.id))}
            />
          </section>
          <section aria-labelledby="agent-detail-instructions">
            <header className="team-heading">
              <h3 id="agent-detail-instructions">{t("teamAgents.instructions")}</h3>
              <button type="button" className="button" ref={editTrigger} onClick={() => setEditing(true)}>
                {t("teamAgents.edit")}
              </button>
            </header>
            <p className="agent-instructions-text">{agent.instructions || t("teamAgents.instructionsEmpty")}</p>
            {notice && (
              <p className="team-agent-feedback" role="status">
                {notice}
              </p>
            )}
          </section>
        </div>
        <aside aria-label={t("teamAgents.connection")}>
          <dl>
            <dt>{t("teamAgents.connection")}</dt>
            <dd>
              <a href={href("team", "models")}>
                {connection ? connectionChoiceLabel(t, connection) : t("team.pages.models")}
              </a>
            </dd>
            <dt>{t("team.connectionStatus")}</dt>
            <dd>{connectionStatus}</dd>
          </dl>
          <a href={href("team", "runtimes")}>{t("team.pages.runtimes")}</a>
        </aside>
      </div>
      {editing && (
        <TeamAgentWizard
          session={session}
          initial={agent}
          initialStep="details"
          onClose={() => {
            setEditing(false);
            editTrigger.current?.focus();
          }}
          onSaved={(saved) => setNotice(t("teamAgents.saved", { name: saved.name }))}
        />
      )}
    </>
  );
}
