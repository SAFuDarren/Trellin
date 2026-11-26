import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/**
 * #355: a project's standing permission to send import material to one connected model (D4).
 * Granted once from the import dialog; listed and withdrawn in Team → Runtimes.
 */
export type IntakeGrant = {
  connection_id: string;
  provider: string;
  model: string;
  researcher_id: string;
  granted_at: string;
};

const grantsKey = (session: string) => ["project-intake-grants", session];

export function useIntakeGrants(session: string) {
  return useQuery({
    queryKey: grantsKey(session),
    queryFn: ({ signal }) => api<{ grants: IntakeGrant[] }>(session, "/api/project-intake/grants", { signal }),
    retry: false,
  });
}

export function useIntakeGrantChange(session: string) {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: ({ action, connectionId }: { action: "grant" | "revoke"; connectionId: string }) =>
      api(session, `/api/project-intake/${action}`, { data: { connection_id: connectionId } }),
    onSettled: () => void queries.invalidateQueries({ queryKey: grantsKey(session) }),
  });
}
