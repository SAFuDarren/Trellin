import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import { useFallbackInterval } from "../api/liveUpdates";
import { Empty, PageHead } from "./SectionLayout";

type Basis = {
  id: string;
  version: number;
  sha256: string;
  kind: string;
  wording: string;
  formal: Record<string, unknown>;
  source?: unknown;
  stale: boolean;
};

type Candidate = {
  id: string;
  sha256: string;
  wording: string;
  formal: Record<string, unknown>;
  provenance_kind: string;
};

type Goal = {
  goal_id: string;
  title: string;
  objective: string;
  domain: string;
  sensitivity: "public" | "restricted";
  frame_sha256: string;
  status: "ready" | "stale" | "blocked" | "selected";
  claim_id: string | null;
  selected_candidate_id?: string | null;
  basis: Basis[];
  discussion: {
    state: "none" | "available" | "unavailable";
    alternatives: { wording: string; reason: string; basis_ids: string[] }[];
  };
  candidates: Candidate[];
};

type Decision = {
  goal_id: string;
  frame_sha256: string;
  candidate_id: string;
  candidate_sha256: string;
  reason: string;
};

const errors = new Set(["invalid", "missing", "stale", "identity", "unavailable"]);

function errorKey(error: unknown) {
  const code = error instanceof ApiError ? (error.code ?? error.message) : "";
  const suffix = code.startsWith("goalReview.") ? code.slice("goalReview.".length) : "";
  return errors.has(suffix) ? suffix : "failed";
}

function record(value: unknown): Record<string, unknown> | null {
  if (value && typeof value === "object" && !Array.isArray(value)) return value as Record<string, unknown>;
  return null;
}

function hasLean(candidate: Candidate) {
  const proposition = record(candidate.formal.lean)?.proposition;
  return typeof proposition === "string" && Boolean(proposition.trim());
}

function display(value: unknown) {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function Formal({ value }: { value: Record<string, unknown> }) {
  const { t } = useTranslation();
  const { lean, ...other } = value;
  const parsed = record(lean);
  if (typeof lean === "string" && Object.keys(other).length === 0) return <pre className="code-block">{lean}</pre>;
  if (!parsed || typeof parsed.proposition !== "string") return <pre className="code-block">{display(value)}</pre>;
  const { proposition, parameters, ...leanFields } = parsed;
  const fields = Object.keys(leanFields).length > 0 ? { ...other, lean: leanFields } : other;
  return (
    <>
      <pre className="code-block">{String(proposition)}</pre>
      {parameters !== undefined && (
        <div className="stack">
          <p className="field-label">{t("detail.parameters")}</p>
          <pre className="code-block">{display(parameters)}</pre>
        </div>
      )}
      {Object.keys(fields).length > 0 && <pre className="code-block">{display(fields)}</pre>}
    </>
  );
}

export function GoalReview({
  session,
  goalId,
  onOpen,
}: {
  session: string;
  goalId: string;
  onOpen: (claimId: string) => void;
}) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const queryKey = ["goal-review", session, goalId];
  const [choice, setChoice] = useState<{ id: string; sha256: string } | null>(null);
  const detail = useQuery({
    queryKey,
    refetchInterval: useFallbackInterval(5000),
    queryFn: async ({ signal }) => {
      const goal = await api<Goal>(session, "/api/goal-review?" + new URLSearchParams({ goal: goalId }), { signal });
      if (goal.goal_id.toLowerCase() !== goalId.toLowerCase()) throw new ApiError(409, "goalReview.stale");
      return goal;
    },
  });
  const action = useMutation({
    mutationFn: ({ decision }: { decision: Decision; location: string }) =>
      api<{ claim_id: string }>(session, "/api/goal-review/select", { data: decision }),
    onSuccess: async (result, requested) => {
      await Promise.all(
        [["inbox", session], ["claims"], ["graph", result.claim_id], ["atlas"], queryKey].map((key) =>
          queries.invalidateQueries({ queryKey: key }),
        ),
      );
      if (window.location.hash === requested.location) onOpen(result.claim_id);
    },
    onError: (error) => {
      if (errorKey(error) === "stale") {
        setChoice(null);
        void queries.invalidateQueries({ queryKey });
        void queries.invalidateQueries({ queryKey: ["inbox", session] });
      }
    },
  });

  if (!detail.data) {
    return (
      <>
        <PageHead title={t("review.kinds.choose_conjecture")} />
        <Empty>{t(detail.isError ? `goalReview.errors.${errorKey(detail.error)}` : "common.loading")}</Empty>
      </>
    );
  }
  const goal = detail.data;
  const approved = goal.basis.every((item) => !item.stale);
  const canConfirm = goal.status === "ready" && approved && !detail.isError && goal.candidates.some(hasLean);
  const candidate = goal.candidates.find(
    (item) => item.id === choice?.id && item.sha256 === choice.sha256 && hasLean(item),
  );
  const versions = new Map(goal.basis.map((item) => [item.id, item]));

  return (
    <>
      <PageHead
        title={goal.title}
        note={t(`goalReview.status.${goal.status}`)}
        actions={
          goal.status === "selected" && goal.claim_id ? (
            <button type="button" className="button" onClick={() => onOpen(goal.claim_id!)}>
              {t("common.openInWorkbench")}
            </button>
          ) : undefined
        }
      />
      <div className="page-body goal-review">
        <section className="block">
          <h3>{t("goalReview.objective")}</h3>
          <p className="page-note">{goal.objective}</p>
          <p className="inline-note">
            {goal.domain} · {t(`goalReview.sensitivity.${goal.sensitivity}`)}
          </p>
        </section>
        {goal.status !== "ready" && (
          <p className="inline-note" role="status">
            {t(`goalReview.statusHelp.${goal.status}`)}
          </p>
        )}
        <section className="block">
          <h3>{t("goalReview.basis")}</h3>
          {goal.basis.length ? (
            <ol className="goal-review-list">
              {goal.basis.map((item) => (
                <li className="stack" key={item.id}>
                  <p>
                    <strong>{item.wording}</strong>
                  </p>
                  <p className="field-label">
                    {t(`kind.${item.kind}`, { defaultValue: item.kind })} ·{" "}
                    {t("goalReview.version", { version: item.version })}
                    {item.stale && <> · {t("goalReview.basisStale")}</>}
                  </p>
                  <Formal value={item.formal} />
                  {item.source != null && (
                    <>
                      <p className="field-label">{t("detail.source")}</p>
                      <pre className="code-block">{display(item.source)}</pre>
                    </>
                  )}
                  <details>
                    <summary>{t("goalReview.recordedVersion")}</summary>
                    <dl className="facts">
                      <dt>{t("reference.basis_version_id")}</dt>
                      <dd className="mono">{item.id}</dd>
                      <dt>{t("goalReview.digest")}</dt>
                      <dd className="mono">{item.sha256}</dd>
                    </dl>
                  </details>
                </li>
              ))}
            </ol>
          ) : (
            <p className="inline-note">{t("goalReview.basisEmpty")}</p>
          )}
        </section>
        {goal.discussion.state !== "none" && (
          <section className="block">
            <h3>{t("goalReview.discussion")}</h3>
            {goal.discussion.state === "unavailable" ? (
              <p className="inline-note">{t("goalReview.discussionUnavailable")}</p>
            ) : (
              <>
                <p className="inline-note">{t("goalReview.discussionHelp")}</p>
                <ol className="goal-review-list">
                  {goal.discussion.alternatives.map((item) => (
                    <li className="stack" key={item.wording}>
                      <p>
                        <strong>{item.wording}</strong>
                      </p>
                      <p>{item.reason}</p>
                      {item.basis_ids.length > 0 && (
                        <>
                          <p className="field-label">{t("goalReview.discussionBasis")}</p>
                          <ul>
                            {item.basis_ids.map((id) => (
                              <li key={id}>{versions.get(id)?.wording ?? id}</li>
                            ))}
                          </ul>
                        </>
                      )}
                    </li>
                  ))}
                </ol>
              </>
            )}
          </section>
        )}
        <section className="block">
          <h3>{t("goalReview.candidates")}</h3>
          {goal.candidates.length ? (
            <form
              className="decision"
              aria-busy={action.isPending}
              onSubmit={(event) => {
                event.preventDefault();
                const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim();
                if (!canConfirm || !candidate || !reason || action.isPending) return;
                action.mutate({
                  decision: {
                    goal_id: goal.goal_id,
                    frame_sha256: goal.frame_sha256,
                    candidate_id: candidate.id,
                    candidate_sha256: candidate.sha256,
                    reason,
                  },
                  location: window.location.hash,
                });
              }}
            >
              {canConfirm && <p className="inline-note">{t("goalReview.confirmHelp")}</p>}
              <ol className="goal-review-list">
                {goal.candidates.map((item) => (
                  <li className="stack" key={item.id}>
                    {canConfirm && hasLean(item) ? (
                      <label className="goal-review-option">
                        <input
                          type="radio"
                          name="conjecture"
                          value={item.id}
                          checked={candidate?.id === item.id}
                          required
                          disabled={action.isPending}
                          onChange={() => setChoice({ id: item.id, sha256: item.sha256 })}
                        />
                        <strong>{item.wording}</strong>
                      </label>
                    ) : (
                      <p>
                        <strong>{item.wording}</strong>
                      </p>
                    )}
                    <p className="field-label">
                      {t(`goalReview.provenance.${item.provenance_kind}`, { defaultValue: item.provenance_kind })}
                      {item.id === goal.selected_candidate_id && <> · {t("goalReview.selectedCandidate")}</>}
                      {!hasLean(item) && <> · {t("goalReview.needsLean")}</>}
                    </p>
                    <Formal value={item.formal} />
                  </li>
                ))}
              </ol>
              {canConfirm && (
                <>
                  <label className="field-label" htmlFor="goal-confirm-reason">
                    {t("review.reason")}
                  </label>
                  <textarea id="goal-confirm-reason" name="reason" rows={2} maxLength={2000} required />
                  <div className="row">
                    <button type="submit" className="button primary" disabled={!candidate || action.isPending}>
                      {t(action.isPending ? "goalReview.confirming" : "goalReview.confirm")}
                    </button>
                  </div>
                </>
              )}
            </form>
          ) : (
            <p className="inline-note">{t("goalReview.candidatesEmpty")}</p>
          )}
        </section>
        {(action.isError || detail.isError) && (
          <p className="status" role="alert" data-error="true">
            {t(`goalReview.errors.${errorKey(action.error ?? detail.error)}`)}
          </p>
        )}
      </div>
    </>
  );
}
