import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import { useFallbackInterval } from "../../api/liveUpdates";
import { href } from "../../shell/route";

type ProofTask = {
  claim_id: string;
  title: string;
  state: "blocked" | "ready" | "authorized" | "running" | "waiting" | "verified" | "stopped" | "failed";
  blocker: string | null;
  can_start: boolean;
  can_stop: boolean;
  local_model_allowed: boolean;
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
  policy: { id: string; fingerprint: string; model_rounds: number } | null;
  receipt: { id: string; kind: "lean" | "counterexample"; review_status: string } | null;
  obligations?: { proposition: string; verified: boolean }[];
  phase?: string | null;
  adopted: boolean;
};

type StartDetails = { token: string; model_rounds: number; reason: string };
type Action =
  | { kind: "start"; details: StartDetails }
  | { kind: "stop"; details: { policy_id: string; reason: string } };

function LeanStatement({ text }: { text: string }) {
  const { t } = useTranslation();
  const code = <pre className="code-block">{text}</pre>;
  return text.length > 240 || text.split("\n").length > 5 ? (
    <details>
      <summary>{t("proofTask.showLean")}</summary>
      {code}
    </details>
  ) : (
    code
  );
}

function StartTaskForm({
  token,
  existingRounds,
  localModelAllowed,
  pending,
  onStart,
}: {
  token: string;
  existingRounds: number | undefined;
  localModelAllowed: boolean;
  pending: boolean;
  onStart: (details: StartDetails) => void;
}) {
  const { t } = useTranslation();
  const [rounds, setRounds] = useState(existingRounds ?? 0);
  const [approved, setApproved] = useState(false);
  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!approved || pending) return;
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim();
    if (!reason) return;
    setApproved(false);
    onStart({ token, model_rounds: rounds, reason });
  }
  return (
    <form className="project-entry-form" onSubmit={start}>
      {localModelAllowed && existingRounds === undefined && (
        <label>
          {t("proofTask.modelRounds")}
          <select
            value={rounds}
            disabled={pending}
            onChange={(event) => {
              setRounds(Number(event.target.value));
              setApproved(false);
            }}
          >
            {Array.from({ length: 9 }, (_, count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
      )}
      <p className="project-entry-note">
        {t(rounds ? "proofTask.localAllowance" : "proofTask.programOnly", { count: rounds })}
      </p>
      <label>
        {t("review.reason")}
        <input name="reason" maxLength={500} required />
      </label>
      <label className="project-entry-check">
        <input
          type="checkbox"
          checked={approved}
          disabled={pending}
          required
          onChange={(event) => setApproved(event.target.checked)}
        />
        {t("proofTask.authorize")}
      </label>
      <div className="project-entry-buttons">
        <button className="button primary" type="submit" disabled={pending || !approved}>
          {t(pending ? "common.loading" : existingRounds === undefined ? "proofTask.start" : "proofTask.resume")}
        </button>
      </div>
    </form>
  );
}

export function ProofTaskPane({ session, claimId }: { session: string; claimId: string }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const task = useQuery({
    queryKey: ["proof-task", session, claimId],
    queryFn: async ({ signal }) => {
      const task = await api<ProofTask>(session, "/api/proof-task?" + new URLSearchParams({ claim: claimId }), {
        signal,
      });
      if (task.claim_id !== claimId) throw new Error("proof_task.scope_mismatch");
      return task;
    },
    refetchInterval: useFallbackInterval(4000),
    retry: false,
  });
  const action = useMutation({
    mutationFn: ({ kind, details }: Action) =>
      api<{ policy_id: string }>(session, "/api/proof-task/" + kind, { data: { claim_id: claimId, ...details } }),
    onSettled: () =>
      Promise.all(
        [["proof-task", session, claimId], ["progress"], ["graph", claimId], ["activity", claimId], ["inbox"]].map(
          (queryKey) => queries.invalidateQueries({ queryKey }),
        ),
      ),
  });
  if (task.isError || !task.data) {
    return (
      <p className="project-entry-status" role={task.isError ? "alert" : "status"}>
        {task.isError
          ? t(`errors.${task.error.message}`, { defaultValue: t("proofTask.loadFailed") })
          : t("common.loading")}
      </p>
    );
  }
  const { state, title, scope, receipt, decisions, blocker, policy, phase, obligations, adopted } = task.data;
  const counterexample = receipt?.kind === "counterexample";
  const stateKey = adopted
    ? counterexample
      ? "proofTask.refutationAdopted"
      : "proofTask.adopted"
    : counterexample
      ? "proofTask.refutation"
      : `proofTask.states.${state}`;
  const phaseText = phase ? t(`proofTask.phases.${phase}`, { defaultValue: "" }) : "";
  function stop(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policy || !task.data?.can_stop || action.isPending) return;
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim();
    if (reason) action.mutate({ kind: "stop", details: { policy_id: policy.id, reason } });
  }
  return (
    <div className="project-entry-form">
      <h3>{title}</h3>
      <p className="project-entry-status" role="status">
        {t(stateKey)}
      </p>
      {phaseText && <p className="project-entry-note">{phaseText}</p>}
      {blocker && (
        <p className="project-entry-note">
          {t(`errors.${blocker.startsWith("proof_task.") ? blocker : "proof_task." + blocker}`, {
            defaultValue: t("proofTask.actionNeeded"),
          })}
        </p>
      )}
      {scope.proposition && (
        <section>
          <h4>{t("proofTask.statement")}</h4>
          <LeanStatement text={scope.proposition} />
        </section>
      )}
      {scope.basis.length > 0 && (
        <details>
          <summary>{t("proofTask.basis")}</summary>
          <ul className="project-entry-items">
            {scope.basis.map((premise, index) => (
              <li className="project-entry-item" key={index}>
                {premise.label && <strong>{premise.label}</strong>}
                {premise.statement && <p>{premise.statement}</p>}
                {premise.formal != null && (
                  <LeanStatement
                    text={
                      typeof premise.formal === "string"
                        ? premise.formal
                        : (premise.formal.lean?.proposition ?? JSON.stringify(premise.formal, null, 2))
                    }
                  />
                )}
              </li>
            ))}
          </ul>
        </details>
      )}
      {scope.omitted_basis > 0 && (
        <p className="project-entry-note">{t("proofTask.moreBasis", { count: scope.omitted_basis })}</p>
      )}
      {obligations && obligations.length > 0 && (
        <details open>
          <summary>{t("proofTask.obligations")}</summary>
          <ul className="project-entry-items">
            {obligations.map(({ proposition, verified }, index) => (
              <li className="project-entry-item" key={index}>
                <p>{t(verified ? "status.verified" : "status.unsolved")}</p>
                <LeanStatement text={proposition} />
              </li>
            ))}
          </ul>
        </details>
      )}
      {decisions.length > 0 && (
        <section>
          <h4>{t("proofTask.decisions")}</h4>
          <ul>
            {decisions.map(({ kind, question }, index) => (
              <li key={index}>
                {question || t(`review.kinds.${kind}`, { defaultValue: t("proofTask.actionNeeded") })}
              </li>
            ))}
          </ul>
        </section>
      )}
      {receipt && (
        <p className="project-entry-note">
          {t("proofTask.evidence", {
            status: t(`status.${receipt.review_status}`, { defaultValue: t("proofTask.unknownReviewStatus") }),
          })}
        </p>
      )}
      {Boolean(decisions.length || receipt) && <a href={href("review")}>{t("proofTask.openReview")}</a>}
      {action.error && (
        <p className="project-entry-status" role="alert">
          {t(`errors.${action.error.message}`, { defaultValue: t("projectEntry.failed") })}
        </p>
      )}
      {task.data.can_start && scope.token && (
        <StartTaskForm
          key={scope.token + ":" + (policy?.fingerprint ?? "") + ":" + task.data.local_model_allowed}
          token={scope.token}
          existingRounds={policy?.model_rounds}
          localModelAllowed={task.data.local_model_allowed}
          pending={action.isPending}
          onStart={(details) => action.mutate({ kind: "start", details })}
        />
      )}
      {task.data.can_stop && policy && (
        <form className="project-entry-form" onSubmit={stop}>
          <label>
            {t("proofTask.stopReason")}
            <input name="reason" maxLength={500} required />
          </label>
          <div className="project-entry-buttons">
            <button className="button" type="submit" disabled={action.isPending}>
              {t("proofTask.stop")}
            </button>
          </div>
        </form>
      )}
    </div>
  );
}
