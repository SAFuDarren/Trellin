import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import { useFallbackInterval } from "./liveUpdates";
import type { ModelConnection } from "./modelConnections";

export type PublicModel = Pick<ModelConnection, "id" | "provider" | "model"> & { fingerprint: string };
type ModelUsage = { input_tokens?: number; output_tokens?: number; cost_usd?: string | number };

export type ProofTask = {
  claim_id: string;
  title: string;
  state: "blocked" | "ready" | "authorized" | "running" | "waiting" | "verified" | "stopped" | "failed";
  blocker: string | null;
  can_start: boolean;
  can_stop: boolean;
  local_model_allowed: boolean;
  external_model_allowed?: boolean;
  scope: {
    token: string | null;
    proposition: string | null;
    basis: {
      label: string | null;
      statement: string | null;
      formal: string | { lean?: { proposition?: string } } | null;
    }[];
    omitted_basis: number;
  };
  decisions: { kind: string; question?: string | null }[];
  policy: {
    id: string;
    fingerprint: string;
    model_rounds: number;
    public_model?: PublicModel | null;
    model_usage?: ModelUsage[];
  } | null;
  receipt: { id: string; kind: "lean" | "counterexample"; review_status: string } | null;
  obligations?: { proposition: string; verified: boolean }[];
  phase?: string | null;
  adopted: boolean;
};

export function useProofTask(session: string, claimId: string | null, streamHealthy = false, interval = 4000) {
  return useQuery({
    queryKey: ["proof-task", session, claimId],
    enabled: claimId !== null,
    queryFn: async ({ signal }) => {
      const task = await api<ProofTask>(session, "/api/proof-task?" + new URLSearchParams({ claim: claimId! }), {
        signal,
      });
      if (task.claim_id !== claimId) throw new Error("proof_task.scope_mismatch");
      return task;
    },
    refetchInterval: useFallbackInterval(interval, streamHealthy),
    retry: false,
  });
}
