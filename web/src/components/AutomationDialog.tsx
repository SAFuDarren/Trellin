import { useId, useLayoutEffect, useRef, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import {
  useApproveAutomation,
  useAutomationTemplates,
  useProposeAutomation,
  type AutomationApproval,
  type AutomationReview,
  type AutomationTemplateId,
} from "../api/automation";
import { ApiError } from "../api/client";

export const AUTOMATION_TEMPLATES: AutomationTemplateId[] = ["lean-reuse", "observe"];

export function automationError(t: TFunction, error: unknown) {
  const code = error instanceof ApiError ? (error.code ?? error.message) : "";
  return t(`automation.errors.${code.replace(/^automation\./, "")}`, {
    defaultValue: t("automation.errors.unavailable"),
  });
}

/** #94: the two template cards, used in the empty Automation page and the dialog's first step. */
export function AutomationTemplateCards({ onChoose }: { onChoose: (template: AutomationTemplateId) => void }) {
  const { t } = useTranslation();
  return (
    <ul className="automation-templates" aria-label={t("automation.chooseTemplate")}>
      {AUTOMATION_TEMPLATES.map((template) => (
        <li key={template}>
          <button type="button" className="automation-template" onClick={() => onChoose(template)}>
            <strong>{t(`automation.templates.${template}.name`)}</strong>
            <span>{t(`automation.templates.${template}.summary`)}</span>
            <span className="automation-template-needs">{t(`automation.templates.${template}.needs`)}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}

type Step = "template" | "claim" | "review";

/**
 * New automation: choose a template, choose a claim it can run on, then review the exact rule.
 * Choosing a claim records a proposal; nothing runs until the researcher approves it here.
 * A proposal made elsewhere opens directly on its review.
 */
export function AutomationDialog({
  session,
  template: initialTemplate,
  proposal,
  onApproved,
  onClose,
}: {
  session: string;
  template?: AutomationTemplateId;
  proposal?: AutomationReview;
  onApproved: (result: AutomationApproval) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const titleId = useId();
  const [template, setTemplate] = useState<AutomationTemplateId | undefined>(proposal?.template ?? initialTemplate);
  const [step, setStep] = useState<Step>(proposal ? "review" : initialTemplate ? "claim" : "template");
  const [review, setReview] = useState<AutomationReview | undefined>(proposal);
  const [reason, setReason] = useState(() => t("automation.reasonDefault"));
  const [failure, setFailure] = useState("");
  const choices = useAutomationTemplates(session, step === "claim");
  const propose = useProposeAutomation(session);
  const approve = useApproveAutomation(session);
  const busy = propose.isPending || approve.isPending;
  const claims = choices.data?.templates.find((entry) => entry.id === template)?.claims ?? [];

  useLayoutEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    heading.current?.focus();
  }, [step]);

  function go(next: Step) {
    setFailure("");
    setStep(next);
  }

  async function chooseClaim(claimId: string) {
    if (!template || busy) return;
    setFailure("");
    try {
      setReview(await propose.mutateAsync({ claim_id: claimId, template }));
      go("review");
    } catch (error) {
      setFailure(automationError(t, error));
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!review || busy || !reason.trim()) return;
    setFailure("");
    try {
      const result = await approve.mutateAsync({
        policy_id: review.policy_id,
        fingerprint: review.fingerprint,
        reason: reason.trim(),
      });
      onApproved(result);
      dialog.current?.close();
    } catch (error) {
      setFailure(automationError(t, error));
    }
  }

  const title =
    step === "template"
      ? "automation.chooseTemplate"
      : step === "claim"
        ? "automation.chooseClaim"
        : "automation.review";
  return (
    <dialog ref={dialog} className="project-entry automation-dialog" aria-labelledby={titleId} onClose={onClose}>
      <header className="project-entry-head">
        <h2 ref={heading} id={titleId} tabIndex={-1}>
          {t(title)}
        </h2>
      </header>
      <form className="project-entry-body project-entry-form" onSubmit={(event) => void submit(event)}>
        {step === "template" && (
          <AutomationTemplateCards
            onChoose={(chosen) => {
              setTemplate(chosen);
              go("claim");
            }}
          />
        )}
        {step === "claim" && template && (
          <>
            <p className="project-entry-note">{t(`automation.templates.${template}.summary`)}</p>
            {choices.isPending ? (
              <p role="status">{t("common.loading")}</p>
            ) : choices.isError ? (
              <p role="alert">{automationError(t, choices.error)}</p>
            ) : claims.length === 0 ? (
              <p className="project-entry-note">{t("automation.noClaims")}</p>
            ) : (
              <ul className="automation-claims" aria-label={t("automation.chooseClaim")}>
                {claims.map((claim) => (
                  <li key={claim.claim_id}>
                    <button
                      type="button"
                      className="automation-claim"
                      disabled={Boolean(claim.missing) || busy}
                      onClick={() => void chooseClaim(claim.claim_id)}
                    >
                      <strong>{claim.title || t("sidebar.untitled")}</strong>
                      {claim.missing && <span>{t(`automation.missing.${claim.missing}`)}</span>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
        {step === "review" && review && (
          <>
            <p className="project-entry-note">{t("automation.reviewIntro")}</p>
            <dl className="facts automation-facts">
              <dt>{t("automation.fields.claim")}</dt>
              <dd>{review.claim_title || t("sidebar.untitled")}</dd>
              <dt>{t("automation.fields.what")}</dt>
              <dd>{t(`automation.templates.${review.template}.summary`)}</dd>
              <dt>{t("automation.fields.when")}</dt>
              <dd>{t(`automation.templates.${review.template}.when`)}</dd>
              <dt>{t("automation.fields.actions")}</dt>
              <dd>{t(review.template === "lean-reuse" ? "automation.actionsLean" : "automation.actionsObserve")}</dd>
              <dt>{t("automation.fields.models")}</dt>
              <dd>
                {review.model_rounds
                  ? t("automation.modelRounds", { count: review.model_rounds })
                  : t("automation.modelsNone")}
              </dd>
              {(review.template === "observe" || Boolean(review.budget_seconds)) && (
                <>
                  <dt>{t("automation.fields.budget")}</dt>
                  <dd>
                    {review.budget_seconds
                      ? t("automation.budgetSeconds", { count: review.budget_seconds })
                      : t("automation.budgetNone")}
                  </dd>
                </>
              )}
            </dl>
            <label>
              {t("automation.reason")}
              <textarea
                value={reason}
                rows={2}
                maxLength={2000}
                required
                disabled={busy}
                onChange={(event) => setReason(event.target.value)}
              />
            </label>
          </>
        )}
        {failure && (
          <p role="alert" className="project-entry-status">
            {failure}
          </p>
        )}
        <footer className="team-agent-footer">
          {step === "claim" && !initialTemplate && (
            <button type="button" className="button" disabled={busy} onClick={() => go("template")}>
              {t("teamAgents.back")}
            </button>
          )}
          {step === "review" && !proposal && (
            <button type="button" className="button" disabled={busy} onClick={() => go("claim")}>
              {t("teamAgents.back")}
            </button>
          )}
          <span className="team-agent-footer-spacer" />
          <button type="button" className="button" disabled={busy} onClick={() => dialog.current?.close()}>
            {t("common.cancel")}
          </button>
          {step === "review" && (
            <button type="submit" className="button primary" disabled={busy || !reason.trim() || !review}>
              {t(approve.isPending ? "automation.approving" : "automation.approve")}
            </button>
          )}
        </footer>
      </form>
    </dialog>
  );
}
