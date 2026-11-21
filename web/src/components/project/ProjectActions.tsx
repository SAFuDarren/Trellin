import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { graphAction, graphActionLabel, type ProjectAction } from "../../workbench/actions";
import type { PremiseReview } from "../../workbench/types";
import { Menu, type FloatingOption } from "../FloatingControls";
import { Chevron } from "../Icons";
import { ClaimCodesPane } from "./ClaimCodesPane";
import { IntakePane } from "./IntakePane";
import { LemmaPlanPane, type LemmaPlanScope } from "./LemmaPlanPane";
import { PremisePane } from "./PremisePane";
import { ProofTaskPane } from "./ProofTaskPane";
import { TheoremPane } from "./TheoremPane";
import "../../styles/project-entry.css";

const localCaptions = {
  lemmas: "lemmaPlan.title",
  premises: "premise.title",
} as const;

type Mode = ProjectAction | keyof typeof localCaptions;

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
  projectAction,
  onProjectAction,
  onOpenPremises,
  onClose,
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
  projectAction: ProjectAction | null;
  onProjectAction: (id: ProjectAction) => void;
  onOpenPremises: (id?: string) => void;
  onClose: () => void;
  onPremiseRecorded: () => Promise<void>;
  onRecorded: (claimId?: string) => void | Promise<void>;
  onGoalRecorded: (claimId: string) => void | Promise<void>;
  onOpenReference: (claimId: string, nodeId: string | null) => void;
}) {
  const { t } = useTranslation();
  const [showLemmas, setShowLemmas] = useState(false);
  const mode: Mode | null = premiseDialog ? "premises" : (projectAction ?? (showLemmas ? "lemmas" : null));
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();

  useEffect(() => {
    if (mode && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [mode]);

  const addTheorem = graphAction(t, "add-theorem", () => onProjectAction("add-theorem"));
  const researchActions: FloatingOption[] = [
    graphAction(t, "organize-research", () => onProjectAction("organize-research")),
  ];
  if (lemmaPlanScope)
    researchActions.push({ id: "lemmas", label: t(localCaptions.lemmas), onSelect: () => setShowLemmas(true) });
  if (premiseReview && (premiseReview.available || premiseReview.proposals.length > 0))
    researchActions.push({ id: "premises", label: t(localCaptions.premises), onSelect: () => onOpenPremises() });
  if (claimCode || claimCodesAvailable)
    researchActions.push(graphAction(t, "research-references", () => onProjectAction("research-references")));

  return (
    <div className="project-actions">
      <button className="button" type="button" onClick={addTheorem.onSelect}>
        {addTheorem.label}
      </button>
      <Menu
        label={t("canvasShell.researchActions")}
        trigger={
          <>
            {t("canvasShell.researchActions")}
            <Chevron />
          </>
        }
        triggerClassName="button workbench-actions-trigger"
        className="workbench-actions-items"
        placement="bottom-end"
        groups={[{ options: researchActions }]}
      />
      <dialog
        ref={dialog}
        className="project-entry"
        aria-labelledby={title}
        onClose={() => {
          setShowLemmas(false);
          onClose();
        }}
      >
        {mode && (
          <>
            <header className="project-entry-head">
              <h2 id={title}>
                {mode === "premises" || mode === "lemmas" ? t(localCaptions[mode]) : graphActionLabel(t, mode)}
              </h2>
              <button className="button" type="button" onClick={() => dialog.current?.close()}>
                {t("projectEntry.close")}
              </button>
            </header>
            <div className="project-entry-body">
              {mode === "organize-research" && <IntakePane session={session} onRecorded={onRecorded} />}
              {mode === "add-theorem" && (
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
              {mode === "proof-task" && claimId && <ProofTaskPane key={claimId} session={session} claimId={claimId} />}
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
              {mode === "research-references" && (
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
