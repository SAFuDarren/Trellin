import { useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../../api/client";
import {
  connectionChoiceLabel,
  connectionModelLabel,
  dispatchableConnection,
  useModelConnections,
  type ModelConnection,
} from "../../api/modelConnections";
import { useProofTask, type ProofTask, type PublicModel } from "../../api/proofTask";
import { Picker } from "../FloatingControls";
import { href } from "../../shell/route";

type AvailableConnection = ModelConnection & PublicModel;
type StartDetails = {
  token: string;
  model_rounds: number;
  reason: string;
  public_model?: Pick<PublicModel, "id" | "fingerprint">;
  confirm_external?: true;
};
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
  policy,
  localModelAllowed,
  externalModelAllowed,
  connections,
  requiresExternal,
  pending,
  onStart,
}: {
  token: string;
  policy: ProofTask["policy"];
  localModelAllowed: boolean;
  externalModelAllowed: boolean;
  connections: AvailableConnection[];
  requiresExternal: boolean;
  pending: boolean;
  onStart: (details: StartDetails) => void;
}) {
  const { t } = useTranslation();
  const [rounds, setRounds] = useState(policy?.model_rounds ?? 0);
  const [approvedScope, setApprovedScope] = useState<string | null>(null);
  const [connectionId, setConnectionId] = useState("");
  const chosenConnection = connections.find(({ id }) => id === connectionId);
  const selected = policy?.public_model ?? chosenConnection;
  const model = selected && `${t(`team.providers.${selected.provider}`)} · ${connectionModelLabel(t, selected)}`;
  const blockedSelection = Boolean((requiresExternal || connectionId) && !selected);
  const authorization = JSON.stringify([token, rounds, selected?.id, selected?.fingerprint]);
  const approved = approvedScope === authorization;

  function start(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!approved || pending || blockedSelection) return;
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim();
    if (!reason) return;
    setApprovedScope(null);
    onStart({
      token,
      model_rounds: rounds,
      reason,
      ...(selected && { public_model: { id: selected.id, fingerprint: selected.fingerprint }, confirm_external: true }),
    });
  }
  return (
    <form className="project-entry-form" onSubmit={start}>
      {externalModelAllowed && !policy && (
        <div className="project-entry-field">
          <label htmlFor="proof-task-model-picker">{t("proofTask.modelConnection")}</label>
          <Picker
            label={t("proofTask.modelConnection")}
            triggerId="proof-task-model-picker"
            triggerClassName="project-entry-picker"
            trigger={
              chosenConnection
                ? connectionChoiceLabel(t, chosenConnection)
                : t(requiresExternal ? "proofTask.modelConnection" : "proofTask.noExternal")
            }
            value={connectionId}
            disabled={pending}
            searchLabel={t("conversations.searchModels")}
            emptyLabel={t("picker.noMatches")}
            groups={[
              {
                options: [
                  { id: "", label: t(requiresExternal ? "proofTask.modelConnection" : "proofTask.noExternal") },
                  ...connections.map((connection) => ({
                    id: connection.id,
                    label: connectionChoiceLabel(t, connection),
                  })),
                ],
              },
            ]}
            onSelect={(id) => {
              if (id === connectionId) return;
              setConnectionId(id);
              setRounds(id ? 1 : 0);
              setApprovedScope(null);
            }}
          />
          {!connections.length && <a href={href("team", "runtimes")}>{t("proofTask.connectModel")}</a>}
        </div>
      )}
      {(localModelAllowed || selected) && !policy && (
        <label>
          {t(selected ? "proofTask.externalRounds" : "proofTask.modelRounds")}
          <select
            value={rounds}
            disabled={pending}
            onChange={(event) => {
              setRounds(Number(event.target.value));
              setApprovedScope(null);
            }}
          >
            {(selected ? [1, 2, 3, 4, 5, 6, 7, 8] : [0, 1, 2, 3, 4, 5, 6, 7, 8]).map((count) => (
              <option key={count} value={count}>
                {count}
              </option>
            ))}
          </select>
        </label>
      )}
      {!selected && !requiresExternal && (
        <p className="project-entry-note">
          {t(rounds ? "proofTask.localAllowance" : "proofTask.programOnly", { count: rounds })}
        </p>
      )}
      <label>
        {t("review.reason")}
        <input name="reason" maxLength={500} required />
      </label>
      <label className="project-entry-check">
        <input
          type="checkbox"
          checked={approved}
          disabled={pending || blockedSelection}
          required
          onChange={(event) => setApprovedScope(event.target.checked ? authorization : null)}
        />
        {t(model ? "proofTask.externalAuthorize" : "proofTask.authorize", { model, count: rounds })}
      </label>
      <div className="project-entry-buttons">
        <button className="button primary" type="submit" disabled={pending || !approved || blockedSelection}>
          {t(pending ? "common.loading" : policy ? "proofTask.resume" : "proofTask.start")}
        </button>
      </div>
    </form>
  );
}

export function ProofTaskPane({ session, claimId }: { session: string; claimId: string }) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const task = useProofTask(session, claimId);
  const choosingConnection =
    task.data?.external_model_allowed === true &&
    task.data.can_start &&
    (task.data.state === "stopped" || !task.data.policy);
  const connections = useModelConnections(session, choosingConnection);
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
  const model = policy?.public_model;
  const usage = policy?.model_usage?.filter(
    ({ input_tokens, output_tokens, cost_usd }) => input_tokens != null || output_tokens != null || cost_usd != null,
  );
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
      {model && (
        <p className="project-entry-note">
          {t("proofTask.selectedModel", {
            model: `${t(`team.providers.${model.provider}`)} · ${connectionModelLabel(t, model)}`,
          })}
        </p>
      )}
      {model && Boolean(usage?.length) && (
        <details>
          <summary>{t("proofTask.usage")}</summary>
          <ol>
            {usage?.map(({ input_tokens, output_tokens, cost_usd }, index) => (
              <li key={index}>
                {[
                  input_tokens != null && t("proofTask.inputTokens", { count: input_tokens }),
                  output_tokens != null && t("proofTask.outputTokens", { count: output_tokens }),
                  cost_usd != null && t("proofTask.cost", { value: cost_usd }),
                ]
                  .filter(Boolean)
                  .join(" · ")}
              </li>
            ))}
          </ol>
        </details>
      )}
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
      {choosingConnection && connections.isError && (
        <p className="project-entry-status" role="alert">
          {t("errors.connection.unavailable")}
        </p>
      )}
      {task.data.can_start && scope.token && (
        <StartTaskForm
          key={scope.token + ":" + (policy?.fingerprint ?? "") + ":" + (state === "stopped")}
          token={scope.token}
          policy={state === "stopped" ? null : policy}
          localModelAllowed={task.data.local_model_allowed}
          externalModelAllowed={task.data.external_model_allowed === true}
          connections={(connections.data?.connections ?? []).filter(dispatchableConnection)}
          requiresExternal={state === "stopped"}
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
