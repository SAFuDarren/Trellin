import { useEffect, useRef, useState, type FormEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../../api/client";
import { kindText } from "../../workbench/text";
import type { PremiseReview, ProposedPremise } from "../../workbench/types";

type Page = { kind: "list" | "compose" } | { kind: "review"; id: string };
type Decision = "reject" | "revise";
type DecisionReceipt = { id: string; revised_claim_id?: string };

export function PremisePane({
  session,
  claimId,
  claimTitle,
  review,
  initialProposal,
  onRecorded,
  onOpenClaim,
}: {
  session: string;
  claimId: string;
  claimTitle: string;
  review?: PremiseReview;
  initialProposal?: string;
  onRecorded: () => Promise<void>;
  onOpenClaim: (id: string) => void;
}) {
  const { t } = useTranslation();
  const active = review?.available === true;
  const [page, setPage] = useState<Page>(initialProposal ? { kind: "review", id: initialProposal } : { kind: "list" });
  const [notice, setNotice] = useState("");
  const heading = useRef<HTMLHeadingElement>(null);
  useEffect(() => heading.current?.focus(), [page.kind]);

  const propose = useMutation({
    mutationFn: (fields: FormData) =>
      api<{ id: string }>(session, "/api/premises/propose", {
        data: {
          claim_id: claimId,
          frame_sha256: review!.frame_sha256,
          wording: String(fields.get("wording") ?? "").trim(),
          lean: String(fields.get("lean") ?? "").trim(),
          question: String(fields.get("question") ?? "").trim(),
          impact: String(fields.get("impact") ?? "").trim(),
        },
      }),
    onSuccess: async () => {
      await onRecorded();
      setNotice(t("premise.saved"));
      setPage({ kind: "list" });
    },
  });
  const decide = useMutation({
    mutationFn: ({ proposal, decision, reason }: { proposal: ProposedPremise; decision: Decision; reason: string }) =>
      api<DecisionReceipt>(session, "/api/premises/decide", {
        data: {
          claim_id: claimId,
          frame_sha256: review!.frame_sha256,
          id: proposal.id,
          sha256: proposal.sha256,
          decision,
          reason,
        },
      }),
    onSuccess: async (_receipt, { decision }) => {
      await onRecorded();
      setNotice(t(decision === "revise" ? "premise.revised" : "premise.rejected"));
    },
  });
  const pending = propose.isPending || decide.isPending;
  const proposal = page.kind === "review" ? review?.proposals.find(({ id }) => id === page.id) : undefined;
  const revisedClaim = decide.data?.revised_claim_id ?? proposal?.revised_claim_id;
  const failure = propose.error ?? decide.error;
  const errorKey = failure instanceof ApiError ? (failure.code ?? failure.message) : "premise.unavailable";

  function show(next: Page) {
    setNotice("");
    propose.reset();
    decide.reset();
    setPage(next);
  }

  function submitProposal(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (review && active && !pending) propose.mutate(new FormData(event.currentTarget));
  }

  function submitDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const decision = (event.nativeEvent as SubmitEvent).submitter?.getAttribute("value");
    if (
      !review ||
      !active ||
      proposal?.state !== "pending" ||
      pending ||
      (decision !== "reject" && decision !== "revise")
    )
      return;
    const reason = String(new FormData(event.currentTarget).get("reason") ?? "").trim();
    if (reason) decide.mutate({ proposal, decision, reason });
  }

  return (
    <div className="project-entry-form">
      <p className="project-entry-note">{t("premise.destination", { title: claimTitle })}</p>
      {notice && (
        <p className="project-entry-note" role="status">
          {notice}
        </p>
      )}
      {failure && (
        <p className="project-entry-note" role="alert">
          {t(errorKey, { defaultValue: t("premise.unavailable") })}
        </p>
      )}
      {!review ? (
        <p className="project-entry-note">{t("premise.unavailable")}</p>
      ) : page.kind === "list" ? (
        <>
          <h3 ref={heading} tabIndex={-1}>
            {t("premise.proposals")}
          </h3>
          {review.proposals.length === 0 ? (
            <p className="project-entry-note">{t("premise.empty")}</p>
          ) : (
            <ul className="project-entry-items">
              {review.proposals.map((entry) => (
                <li className="project-entry-item" key={entry.id}>
                  <p>{entry.wording}</p>
                  <span className="project-entry-note">{t(`premise.states.${entry.state}`)}</span>
                  <div className="project-entry-buttons">
                    <button className="button" type="button" onClick={() => show({ kind: "review", id: entry.id })}>
                      {t(entry.state === "pending" ? "premise.review" : "premise.details")}
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
          {active && (
            <div className="project-entry-buttons">
              <button className="button" type="button" onClick={() => show({ kind: "compose" })}>
                {t("premise.propose")}
              </button>
            </div>
          )}
        </>
      ) : page.kind === "compose" ? (
        <form className="project-entry-form" onSubmit={submitProposal}>
          <h3 ref={heading} tabIndex={-1}>
            {t("premise.propose")}
          </h3>
          <p className="project-entry-note">{t(active ? "premise.hold" : "premise.stale")}</p>
          <label>
            {t("premise.wording")}
            <textarea name="wording" rows={2} maxLength={4000} disabled={pending || !active} required />
          </label>
          <label>
            {t("projectEntry.lean")}
            <textarea
              className="project-entry-lean"
              name="lean"
              rows={3}
              maxLength={16000}
              spellCheck={false}
              disabled={pending || !active}
              required
            />
          </label>
          <label>
            {t("premise.question")}
            <textarea name="question" rows={2} maxLength={900} disabled={pending || !active} required />
          </label>
          <label>
            {t("premise.impact")}
            <textarea name="impact" rows={2} maxLength={2000} disabled={pending || !active} required />
          </label>
          <div className="project-entry-buttons">
            <button className="button" type="button" disabled={pending} onClick={() => show({ kind: "list" })}>
              {t("premise.back")}
            </button>
            <button className="button primary" type="submit" disabled={pending || !active}>
              {t(pending ? "common.loading" : "premise.save")}
            </button>
          </div>
        </form>
      ) : (
        <>
          <h3 ref={heading} tabIndex={-1}>
            {t("premise.review")}
          </h3>
          {proposal && (
            <>
              <div className="project-entry-item">
                <h4>{t("premise.wording")}</h4>
                <p>{proposal.wording}</p>
                <pre className="code-block">{proposal.lean}</pre>
                <dl className="facts">
                  <dt>{t("premise.question")}</dt>
                  <dd>{proposal.question}</dd>
                  <dt>{t("premise.impact")}</dt>
                  <dd>{proposal.impact}</dd>
                  <dt>{t("review.status")}</dt>
                  <dd>{t(`premise.states.${proposal.state}`)}</dd>
                  {proposal.reason && (
                    <>
                      <dt>{t("review.reason")}</dt>
                      <dd>{proposal.reason}</dd>
                    </>
                  )}
                </dl>
              </div>
              <div className="project-entry-item">
                <h4>{t("premise.target")}</h4>
                <p>{review.target.wording}</p>
                <pre className="code-block">{review.target.lean}</pre>
                <h4>{t("premise.basis")}</h4>
                {review.basis.length === 0 ? (
                  <p className="project-entry-note">{t("premise.noBasis")}</p>
                ) : (
                  <ul className="project-entry-items">
                    {review.basis.map((item) => (
                      <li key={item.id}>
                        <p>{[kindText(t, item.kind), item.wording].join(" · ")}</p>
                        {item.lean && <pre className="code-block">{item.lean}</pre>}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
              {proposal.state === "pending" && active && !decide.data && (
                <form className="project-entry-form" onSubmit={submitDecision}>
                  <p className="project-entry-note">{t("premise.reviseHint")}</p>
                  <label>
                    {t("review.reason")}
                    <textarea name="reason" rows={2} maxLength={2000} disabled={pending} required />
                  </label>
                  <div className="project-entry-buttons">
                    <button className="button" type="submit" value="reject" disabled={pending}>
                      {t("premise.reject")}
                    </button>
                    <button className="button" type="submit" value="revise" disabled={pending}>
                      {t("premise.revise")}
                    </button>
                  </div>
                  {pending && (
                    <p className="project-entry-note" role="status">
                      {t("common.loading")}
                    </p>
                  )}
                </form>
              )}
            </>
          )}
          {!proposal && <p className="project-entry-note">{t("premise.stale")}</p>}
          <div className="project-entry-buttons">
            <button className="button" type="button" disabled={pending} onClick={() => show({ kind: "list" })}>
              {t("premise.back")}
            </button>
            {revisedClaim && (
              <button className="button" type="button" onClick={() => onOpenClaim(revisedClaim)}>
                {t("premise.openRevised")}
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}
