import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api, ApiError } from "../api/client";
import type { InboxItem } from "../api/sections";
import { reviewItemKindLabel } from "../shell/reviewItems";
import { formatShortcut } from "../shortcuts";
import { timeText } from "../workbench/text";
import { ReviewSigner } from "./project/ReviewFields";
import { formalText, rememberReviewer, savedReviewer } from "./project/review";
import { Empty, PageHead } from "./SectionLayout";
import "../styles/intake-review.css";

// #340: confirm a starting research scope in the workbench, whether the draft came from the
// workbench, the command line, an Agent or a frozen target.

type Formal = Record<string, unknown> | null;
type IntakeItem = {
  id: string;
  kind: string;
  natural: string;
  formal: Formal;
  question?: string;
  status: string;
  requires_decision: boolean;
  matched_item?: { version: number; natural: string; formal: Formal; stale: boolean };
};
export type IntakeDetail = {
  id: string;
  sha256: string;
  title: string;
  domain: string;
  objective: string;
  sensitivity: string;
  status: "pending" | "confirmed" | "superseded" | "stale";
  claim_id: string | null;
  target: { statement: string; formal: Formal } | null;
  items: IntakeItem[];
  outstanding_count: number;
  reused_count: number;
};
type Choice = "accept" | "omit";
type Decision = { action: Choice; reason: string; formal?: { lean: { proposition: string } } };

/** The workbench reports intake failures as fixed keys, such as `intakeReview.stale`. */
function errorKey(error: unknown): string {
  if (error instanceof ApiError) return error.code ?? error.message;
  return error instanceof Error ? error.message : String(error);
}

function errorText(t: (key: string, options?: Record<string, unknown>) => string, error: unknown) {
  const key = errorKey(error);
  return key.startsWith("intakeReview.") ? t(`errors.${key}`) : t("errors.intakeReview.unavailable");
}

export function IntakeReview({
  session,
  item,
  signer,
  onOpen,
  onDecided,
}: {
  session: string;
  item: InboxItem;
  signer?: string;
  onOpen: (claimId: string) => void;
  onDecided: (message: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const queries = useQueryClient();
  const detail = useQuery({
    queryKey: ["intake-review", session, item.item_id],
    queryFn: ({ signal }) =>
      api<IntakeDetail>(session, "/api/intake-review?intake=" + encodeURIComponent(item.item_id), { signal }),
    retry: false,
  });
  const [choices, setChoices] = useState<Record<string, Choice>>({});
  const [lean, setLean] = useState<Record<string, string>>({});
  const [reviewer, setReviewer] = useState(() => (signer ? "" : savedReviewer()));
  const [reason, setReason] = useState("");
  const [state, setState] = useState<{ sending?: boolean; message?: string; error?: boolean }>({});
  const sending = useRef(false);

  const intake = detail.data;
  const title = intake?.title || (typeof item.detail?.title === "string" ? item.detail.title : "");
  const head = (
    <PageHead
      title={title || reviewItemKindLabel(t, item)}
      note={`${reviewItemKindLabel(t, item)} · ${timeText(i18n.language, item.waiting_since, true)}`}
      actions={
        intake?.claim_id ? (
          <button type="button" className="button" onClick={() => onOpen(intake.claim_id!)}>
            {t("common.openInWorkbench")}
          </button>
        ) : undefined
      }
    />
  );
  if (detail.isPending)
    return (
      <>
        {head}
        <Empty>{t("common.loading")}</Empty>
      </>
    );
  if (!intake)
    return (
      <>
        {head}
        <Empty>{errorText(t, detail.error)}</Empty>
      </>
    );

  const outstanding = intake.items.filter((entry) => entry.requires_decision);
  const reused = intake.items.filter((entry) => !entry.requires_decision);
  const open = intake.status === "pending";

  const confirm = async () => {
    if (sending.current) return;
    const rationale = reason.trim();
    const actor = signer ?? reviewer.trim();
    if (outstanding.some((entry) => !choices[entry.id])) {
      setState({ message: t("intakeReview.needDecisions"), error: true });
      return;
    }
    const missingLean = outstanding.find(
      (entry) => choices[entry.id] === "accept" && !entry.formal && !lean[entry.id]?.trim(),
    );
    if (missingLean) {
      setState({ message: t("intakeReview.needLean"), error: true });
      return;
    }
    if (!rationale || !actor) {
      setState({ message: t(signer ? "review.needReason" : "review.needNameAndReason"), error: true });
      return;
    }
    const decisions: Record<string, Decision> = {};
    for (const entry of outstanding) {
      const action = choices[entry.id]!;
      const supplied = lean[entry.id]?.trim();
      decisions[entry.id] = {
        action,
        reason: rationale,
        ...(action === "accept" && !entry.formal && supplied ? { formal: { lean: { proposition: supplied } } } : {}),
      };
    }
    sending.current = true;
    setState({ sending: true });
    try {
      if (!signer) rememberReviewer(actor);
      await api(session, "/api/intake-review/confirm", {
        data: { id: intake.id, sha256: intake.sha256, decisions, reason: rationale, ...(signer ? {} : { actor }) },
      });
      setState({});
      onDecided(t("intakeReview.confirmed"));
      for (const key of ["inbox", "claims", "atlas", "project-canvas-claims", "intake-review", "decision-records"])
        void queries.invalidateQueries({ queryKey: [key] });
    } catch (error) {
      setState({ message: errorText(t, error), error: true });
      if (errorKey(error) === "intakeReview.stale")
        void queries.invalidateQueries({ queryKey: ["intake-review", session, item.item_id] });
    } finally {
      sending.current = false;
    }
  };

  return (
    <>
      {head}
      <div className="page-body intake-review">
        <p className="page-note">{t("intakeReview.explain")}</p>
        <dl className="facts">
          <dt>{t("intakeReview.domain")}</dt>
          <dd>{intake.domain}</dd>
          <dt>{t("intakeReview.objective")}</dt>
          <dd>{intake.objective}</dd>
          <dt>{t("intakeReview.sensitivity")}</dt>
          <dd>{t(`goalReview.sensitivity.${intake.sensitivity}`, { defaultValue: intake.sensitivity })}</dd>
        </dl>
        {intake.target && (
          <section className="intake-target" aria-label={t("intakeReview.target")}>
            <h3>{t("intakeReview.target")}</h3>
            <p>{intake.target.statement}</p>
            {intake.target.formal && <pre className="code-block">{formalText(intake.target.formal)}</pre>}
          </section>
        )}
        {!open && (
          <p className="intake-closed" role="status">
            {t(`intakeReview.closed.${intake.status}`)}
          </p>
        )}
        {outstanding.length > 0 && (
          <section aria-label={t("intakeReview.toDecide", { count: outstanding.length })}>
            <h3>{t("intakeReview.toDecide", { count: outstanding.length })}</h3>
            <ol className="intake-items">
              {outstanding.map((entry, index) => {
                const statement = formalText(entry.formal);
                const label = t("intakeReview.itemLabel", {
                  index: index + 1,
                  kind: t(`projectEntry.intake.kinds.${entry.kind}`, { defaultValue: entry.kind }),
                });
                return (
                  <li key={entry.id} className="intake-item" data-choice={choices[entry.id] ?? ""}>
                    <header>
                      <strong>{label}</strong>
                      <span className="intake-status" data-status={entry.status}>
                        {t(`intakeReview.status.${entry.status}`, { defaultValue: "" })}
                      </span>
                    </header>
                    <blockquote>{entry.natural}</blockquote>
                    {entry.question && <p className="intake-question">{entry.question}</p>}
                    {statement && <pre className="code-block">{statement}</pre>}
                    {open && (
                      <>
                        <fieldset className="evidence-decision-choice" disabled={state.sending}>
                          <legend className="sr-only">{t("intakeReview.decisionFor", { item: label })}</legend>
                          {(["accept", "omit"] as const).map((action) => (
                            <label key={action}>
                              <input
                                type="radio"
                                name={`intake-${entry.id}`}
                                value={action}
                                checked={choices[entry.id] === action}
                                onChange={() => setChoices({ ...choices, [entry.id]: action })}
                              />
                              {t(`intakeReview.${action}`)}
                            </label>
                          ))}
                        </fieldset>
                        {!entry.formal && choices[entry.id] === "accept" && (
                          <label className="intake-lean">
                            {t("intakeReview.leanFor", { item: label })}
                            <textarea
                              rows={2}
                              maxLength={4000}
                              value={lean[entry.id] ?? ""}
                              onChange={(event) => setLean({ ...lean, [entry.id]: event.target.value })}
                            />
                          </label>
                        )}
                      </>
                    )}
                  </li>
                );
              })}
            </ol>
          </section>
        )}
        {reused.length > 0 && (
          <details className="intake-reused">
            <summary>{t("intakeReview.reused", { count: reused.length })}</summary>
            <ul>
              {reused.map((entry) => (
                <li key={entry.id}>
                  <span>{entry.natural}</span>
                  <small>
                    {[
                      t(`projectEntry.intake.kinds.${entry.kind}`, { defaultValue: entry.kind }),
                      entry.matched_item && t("intakeReview.version", { version: entry.matched_item.version }),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                  </small>
                  {entry.matched_item?.stale && <em className="intake-stale">{t("intakeReview.changed")}</em>}
                </li>
              ))}
            </ul>
          </details>
        )}
        {open && (
          <form
            className="decision"
            onSubmit={(event) => {
              event.preventDefault();
              void confirm();
            }}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || (!event.metaKey && !event.ctrlKey) || event.nativeEvent.isComposing) return;
              event.preventDefault();
              if (!event.repeat) event.currentTarget.requestSubmit();
            }}
          >
            {signer ? (
              <ReviewSigner name={signer} />
            ) : (
              <>
                <label className="field-label" htmlFor={`intake-reviewer-${intake.id}`}>
                  {t("review.reviewer")}
                </label>
                <input
                  id={`intake-reviewer-${intake.id}`}
                  className="reviewer"
                  value={reviewer}
                  maxLength={120}
                  onChange={(event) => setReviewer(event.target.value)}
                />
              </>
            )}
            <label className="field-label" htmlFor={`intake-reason-${intake.id}`}>
              {t("review.reason")}
            </label>
            <textarea
              id={`intake-reason-${intake.id}`}
              rows={3}
              maxLength={2000}
              value={reason}
              placeholder={t("intakeReview.reasonHint")}
              onChange={(event) => setReason(event.target.value)}
            />
            <div className="row">
              <button
                type="submit"
                className="button primary"
                aria-keyshortcuts="Meta+Enter Control+Enter"
                disabled={state.sending}
              >
                {t("intakeReview.submit")}
              </button>
              <kbd>{formatShortcut("Primary+Enter")}</kbd>
              {state.message && (
                <span className="status" role="status" data-error={String(Boolean(state.error))}>
                  {state.message}
                </span>
              )}
            </div>
          </form>
        )}
      </div>
    </>
  );
}
