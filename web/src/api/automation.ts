import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** #94: automation rules created, approved and stopped from the workbench. */
export type AutomationTemplateId = "lean-reuse" | "observe";
export type AutomationMissing = "basis" | "questions" | "lean_statement";
export type AutomationTemplates = {
  templates: {
    id: AutomationTemplateId;
    claims: { claim_id: string; title: string | null; missing: AutomationMissing | null }[];
  }[];
};
export type AutomationReview = {
  policy_id: string;
  claim_id: string;
  claim_title: string | null;
  fingerprint: string;
  state: string | null;
  approved: boolean;
  stopped: boolean;
  template: AutomationTemplateId;
  metric: string | null;
  actions: string[] | null;
  model_rounds: number;
  budget_seconds: number | null;
  max_rounds: number | null;
};
export type AutomationApproval = AutomationReview & { run: "started" | "waiting" | "failed" };

export function useAutomationTemplates(session: string, enabled = true) {
  return useQuery({
    queryKey: ["automation-templates", session],
    queryFn: ({ signal }) => api<AutomationTemplates>(session, "/api/automation", { signal }),
    enabled,
    retry: false,
  });
}

function useAutomationWrite<Input, Output>(session: string, path: string) {
  const queries = useQueryClient();
  return useMutation({
    mutationFn: (data: Input) => api<Output>(session, path, { data }),
    onSettled: () => {
      void queries.invalidateQueries({ queryKey: ["progress"] });
      void queries.invalidateQueries({ queryKey: ["automation-templates", session] });
    },
  });
}

export function useProposeAutomation(session: string) {
  return useAutomationWrite<{ claim_id: string; template: AutomationTemplateId }, AutomationReview>(
    session,
    "/api/automation/propose",
  );
}

export function useApproveAutomation(session: string) {
  return useAutomationWrite<{ policy_id: string; fingerprint: string; reason: string }, AutomationApproval>(
    session,
    "/api/automation/approve",
  );
}

export function useStopAutomation(session: string) {
  return useAutomationWrite<{ policy_id: string; reason: string }, AutomationReview>(session, "/api/automation/stop");
}
