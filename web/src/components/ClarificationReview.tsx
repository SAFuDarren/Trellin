import { Fragment } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import { href } from "../shell/route";
import { Empty, PageHead } from "./SectionLayout";

type ClarificationState = "waiting" | "answered" | "ready" | "continued" | "confirmed" | "stale" | "dismissed";
type Clarification = {
  id: string;
  title: string;
  statement: string;
  questions: string[];
  answers: string[] | null;
  reason: string | null;
  kind: "target" | "conjecture";
  sensitivity: "restricted" | "public";
  status: ClarificationState;
  next_id: string | null;
  proposal: { token: string; proposition: string; parameters: { name: string; type: string }[] } | null;
  accepted: { id: string; kind: "target" | "conjecture"; claim_id: string | null } | null;
};

type Action =
  | { path: "answer"; data: { answers: string[]; reason: string } }
  | { path: "prepare"; data: { scope: "local" | "public" } }
  | { path: "confirm"; data: { token: string; reason: string } };

const errors = new Set([
  "invalid",
  "not_found",
  "identity_required",
  "unavailable",
  "stale",
  "scope",
  "busy",
  "prepare_failed",
]);
function errorKey(error: unknown) {
  const code = error instanceof ApiError ? error.code : undefined;
  const suffix = code?.startsWith("clarification.") ? code.slice("clarification.".length) : undefined;
  return suffix && errors.has(suffix) ? suffix : "failed";
}

export function ClarificationReview({
  session,
  workspace,
  id,
  onConfirmed,
}: {
  session: string;
  workspace: string;
  id: string;
  onConfirmed: (kind: Clarification["kind"]) => void;
}) {
  const { t } = useTranslation();
  const queries = useQueryClient();
  const route = `/api/v1/workspaces/${encodeURIComponent(workspace)}/clarifications/${encodeURIComponent(id)}`;
  const queryKey = ["clarification", session, id];
  const detail = useQuery({
    queryKey,
    queryFn: ({ signal }) => api<Clarification>(session, route, { signal }),
  });
  const action = useMutation({
    mutationFn: ({ path, data }: Action) => api<Clarification>(session, `${route}/${path}`, { data }),
    onSuccess: (updated, requested) => {
      queries.setQueryData(queryKey, updated);
      if (requested.path === "confirm" && updated.status === "confirmed") onConfirmed(updated.kind);
      if (requested.path === "prepare" && updated.next_id) window.location.hash = href("review", updated.next_id);
      void queries.invalidateQueries({ queryKey: ["inbox", session] });
    },
    onError: (error) => {
      if (errorKey(error) === "stale") {
        void queries.invalidateQueries({ queryKey });
        void queries.invalidateQueries({ queryKey: ["inbox", session] });
      }
    },
  });

  if (!detail.data) {
    return (
      <>
        <PageHead title={t("review.kinds.clarify_formalization")} />
        <Empty>{t(detail.isError ? `clarifications.errors.${errorKey(detail.error)}` : "common.loading")}</Empty>
      </>
    );
  }

  const item = detail.data;
  const state = item.status;
  const proposal = item.proposal;
  const publicMaterial = item.sensitivity === "public";
  return (
    <>
      <PageHead title={item.title} note={t(`clarifications.status.${state}`)} />
      <div className="page-body">
        <h3 className="field-label">{t("clarifications.original")}</h3>
        <p className="page-note">{item.statement}</p>
        {state === "waiting" ? (
          <form
            className="decision"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              action.mutate({
                path: "answer",
                data: {
                  answers: item.questions.map((_, index) => String(data.get(`answer-${index}`) ?? "").trim()),
                  reason: String(data.get("reason") ?? "").trim(),
                },
              });
            }}
          >
            {item.questions.map((question, index) => (
              <div key={index}>
                <label className="field-label" htmlFor={`clarification-answer-${index}`}>
                  {question}
                </label>
                <textarea
                  id={`clarification-answer-${index}`}
                  name={`answer-${index}`}
                  rows={2}
                  maxLength={1200}
                  required
                />
              </div>
            ))}
            <label className="field-label" htmlFor="clarification-answer-reason">
              {t("review.reason")}
            </label>
            <textarea id="clarification-answer-reason" name="reason" rows={2} maxLength={2000} required />
            <div className="row">
              <button type="submit" className="button primary" disabled={action.isPending}>
                {t("clarifications.saveAnswers")}
              </button>
            </div>
          </form>
        ) : (
          item.answers && (
            <dl className="facts">
              {item.questions.map((question, index) => (
                <Fragment key={index}>
                  <dt>{question}</dt>
                  <dd>{item.answers?.[index]}</dd>
                </Fragment>
              ))}
            </dl>
          )
        )}

        {state === "answered" && (
          <form
            className="decision"
            onSubmit={(event) => {
              event.preventDefault();
              action.mutate({ path: "prepare", data: { scope: publicMaterial ? "public" : "local" } });
            }}
          >
            <p className="inline-note">
              {t(publicMaterial ? "clarifications.publicPreparation" : "clarifications.localPreparation")}
            </p>
            {publicMaterial && (
              <label className="field-label">
                <input type="checkbox" required /> {t("clarifications.confirmPublic")}
              </label>
            )}
            <div className="row">
              <button type="submit" className="button primary" disabled={action.isPending}>
                {t(action.isPending ? "clarifications.preparing" : "clarifications.prepare")}
              </button>
            </div>
          </form>
        )}

        {proposal && (
          <section>
            <h3 className="field-label">{t("clarifications.proposal")}</h3>
            {proposal.parameters.length > 0 && (
              <>
                <p className="inline-note">{t("detail.parameters")}</p>
                <pre className="code-block">
                  {proposal.parameters.map((parameter) => `${parameter.name} : ${parameter.type}`).join("\n")}
                </pre>
              </>
            )}
            <pre className="code-block">{proposal.proposition}</pre>
          </section>
        )}

        {state === "ready" && proposal && (
          <form
            className="decision"
            onSubmit={(event) => {
              event.preventDefault();
              const data = new FormData(event.currentTarget);
              action.mutate({
                path: "confirm",
                data: { token: proposal.token, reason: String(data.get("reason") ?? "").trim() },
              });
            }}
          >
            <p className="inline-note">{t(`clarifications.explain.${item.kind}`)}</p>
            <label className="field-label" htmlFor="clarification-confirm-reason">
              {t("review.reason")}
            </label>
            <textarea id="clarification-confirm-reason" name="reason" rows={2} maxLength={2000} required />
            <div className="row">
              <button type="submit" className="button primary" disabled={action.isPending}>
                {t(`clarifications.confirm.${item.kind}`)}
              </button>
            </div>
          </form>
        )}
        {item.next_id && (
          <a href={href("review", item.next_id)} className="button">
            {t("clarifications.next")}
          </a>
        )}
        {action.isError && (
          <p className="status" role="alert" data-error="true">
            {t(`clarifications.errors.${errorKey(action.error)}`)}
          </p>
        )}
      </div>
    </>
  );
}
