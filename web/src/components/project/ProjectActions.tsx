import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { PremiseReview } from "../../workbench/types";
import { ClaimCodesPane } from "./ClaimCodesPane";
import { IntakePane } from "./IntakePane";
import { LemmaPlanPane, type LemmaPlanScope } from "./LemmaPlanPane";
import { PremisePane } from "./PremisePane";
import { ProofTaskPane } from "./ProofTaskPane";
import { TheoremPane } from "./TheoremPane";
import "../../styles/project-entry.css";

const captions = {
  intake: "projectEntry.organize",
  theorem: "projectEntry.addTheorem",
  proof: "proofTask.title",
  lemmas: "lemmaPlan.title",
  premises: "premise.title",
  references: "claimCodes.title",
} as const;

type Mode = keyof typeof captions;

/** Project actions and proof work scoped to the selected claim. */
export function ProjectActions({
  session,
  claimId,
  claimCode,
  claimCodesAvailable,
  lemmaPlanScope,
  claimTitle,
  premiseReview,
  premiseDialog,
  overview,
  onToggleOverview,
  onOpenPremises,
  onClosePremises,
  onPremiseRecorded,
  onRecorded,
  onGoalRecorded,
  onOpenReference,
}: {
  session: string;
  claimId: string | null;
  claimCode?: string | null;
  claimCodesAvailable: boolean;
  lemmaPlanScope: LemmaPlanScope | null;
  claimTitle: string;
  premiseReview?: PremiseReview;
  premiseDialog: { id?: string } | null;
  overview?: boolean;
  onToggleOverview: () => void;
  onOpenPremises: (id?: string) => void;
  onClosePremises: () => void;
  onPremiseRecorded: () => Promise<void>;
  onRecorded: (claimId?: string) => void | Promise<void>;
  onGoalRecorded: (claimId: string) => void | Promise<void>;
  onOpenReference: (claimId: string, nodeId: string | null) => void;
}) {
  const { t } = useTranslation();
  const [localMode, setMode] = useState<Exclude<Mode, "premises"> | null>(null);
  const mode = premiseDialog ? "premises" : localMode;
  const dialog = useRef<HTMLDialogElement>(null);
  const actions = useRef<HTMLDetailsElement>(null);
  const title = useId();

  useEffect(() => {
    if (mode && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [mode]);

  useEffect(() => {
    function outside(event: PointerEvent) {
      const menu = actions.current;
      if (menu?.open && event.target instanceof Node && !menu.contains(event.target)) menu.open = false;
    }
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, []);

  const closeActions = () => {
    if (!actions.current?.open) return;
    actions.current.open = false;
    actions.current.querySelector("summary")?.focus();
  };
  const show = (next: Mode) => {
    closeActions();
    if (next === "premises") onOpenPremises();
    else setMode(next);
  };

  return (
    <div className="project-actions">
      <button className="button" type="button" onClick={() => show("theorem")}>
        {t("projectEntry.addTheorem")}
      </button>
      {claimId && (
        <button className="button" type="button" onClick={() => show("proof")}>
          {t("proofTask.title")}
        </button>
      )}
      <details
        className="workbench-actions-menu"
        ref={actions}
        onKeyDown={(event) => {
          if (event.key !== "Escape" || !event.currentTarget.open) return;
          event.preventDefault();
          closeActions();
        }}
      >
        <summary className="button">{t("canvasShell.researchActions")}</summary>
        <div className="workbench-actions-items">
          <button type="button" onClick={() => show("intake")}>
            {t("projectEntry.organize")}
          </button>
          {lemmaPlanScope && (
            <button type="button" onClick={() => show("lemmas")}>
              {t("lemmaPlan.title")}
            </button>
          )}
          {premiseReview && (premiseReview.available || premiseReview.proposals.length > 0) && (
            <button type="button" onClick={() => show("premises")}>
              {t("premise.title")}
            </button>
          )}
          {(claimCode || claimCodesAvailable) && (
            <button type="button" onClick={() => show("references")}>
              {t("claimCodes.title")}
            </button>
          )}
          {overview !== undefined && (
            <button
              type="button"
              aria-pressed={overview}
              onClick={() => {
                onToggleOverview();
                closeActions();
              }}
            >
              {t("canvasShell.proofOverview")}
            </button>
          )}
        </div>
      </details>
      <dialog
        ref={dialog}
        className="project-entry"
        aria-labelledby={title}
        onClose={() => {
          setMode(null);
          onClosePremises();
        }}
      >
        {mode && (
          <>
            <header className="project-entry-head">
              <h2 id={title}>{t(captions[mode])}</h2>
              <button className="button" type="button" onClick={() => dialog.current?.close()}>
                {t("projectEntry.close")}
              </button>
            </header>
            <div className="project-entry-body">
              {mode === "intake" && <IntakePane session={session} onRecorded={onRecorded} />}
              {mode === "theorem" && (
                <TheoremPane
                  session={session}
                  onRecorded={(claimId) => {
                    if (claimId) {
                      dialog.current?.close();
                      onGoalRecorded(claimId);
                    } else {
                      onRecorded();
                    }
                  }}
                />
              )}
              {mode === "proof" && claimId && <ProofTaskPane key={claimId} session={session} claimId={claimId} />}
              {mode === "lemmas" &&
                (lemmaPlanScope ? (
                  <LemmaPlanPane key={lemmaPlanScope.statementId} session={session} scope={lemmaPlanScope} />
                ) : (
                  <p role="status">{t("lemmaPlan.stale")}</p>
                ))}
              {mode === "premises" && claimId && (
                <PremisePane
                  key={claimId}
                  session={session}
                  claimId={claimId}
                  claimTitle={claimTitle}
                  review={premiseReview}
                  initialProposal={premiseDialog?.id}
                  onRecorded={onPremiseRecorded}
                  onOpenClaim={(id) => {
                    dialog.current?.close();
                    onOpenReference(id, null);
                  }}
                />
              )}
              {mode === "references" && (
                <ClaimCodesPane
                  key={claimId ?? "workspace"}
                  session={session}
                  claim={claimId && claimCode ? { id: claimId, code: claimCode } : null}
                  onSaved={onRecorded}
                  onOpened={(id, nodeId) => {
                    dialog.current?.close();
                    onOpenReference(id, nodeId);
                  }}
                />
              )}
            </div>
          </>
        )}
      </dialog>
    </div>
  );
}
