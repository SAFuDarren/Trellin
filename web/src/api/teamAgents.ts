import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError, api } from "./client";

export const AGENT_AVATARS = ["initials", "book", "compass", "spark"] as const;

export type TeamAgent = {
  id: string;
  revision: number;
  connection_id: string;
  name: string;
  description: string;
  avatar: (typeof AGENT_AVATARS)[number];
  instructions: string;
  starters: string[];
};

const teamAgentsKey = (session: string) => ["team-agents", session];

export function useTeamAgents(session: string, enabled = true) {
  return useQuery({
    queryKey: teamAgentsKey(session),
    queryFn: ({ signal }) => api<{ agents: TeamAgent[] }>(session, "/api/team-agents", { signal }),
    enabled,
    retry: false,
  });
}

export function useSaveTeamAgent(session: string) {
  const queries = useQueryClient();
  const key = teamAgentsKey(session);
  return useMutation({
    mutationFn: (agent: TeamAgent) => api<{ agent: TeamAgent }>(session, "/api/team-agents/save", { data: agent }),
    onSuccess: async ({ agent }) => {
      await queries.cancelQueries({ queryKey: key });
      queries.setQueryData<{ agents: TeamAgent[] }>(key, (current) => ({
        agents: [...(current?.agents ?? []).filter((item) => item.id !== agent.id), agent],
      }));
      void queries.invalidateQueries({ queryKey: key });
    },
    onError: (error) => {
      if (error instanceof ApiError && (error.code ?? error.message) === "team_agents.conflict") {
        void queries.invalidateQueries({ queryKey: key });
      }
    },
  });
}
