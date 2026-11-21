import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { PremiseReview } from "../../workbench/types";
import { Menu } from "../FloatingControls";
import { Chevron } from "../Icons";
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
  proofDialog,
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
  proofDialog: boolean;
  onOpenPremises: (id?: string) => void;
  onClose: () => void;
  onPremiseRecorded: () => Promise<void>;
  onRecorded: (claimId?: string) => void | Promise<void>;
  onGoalRecorded: (claimId: string) => void | Promise<void>;
  onOpenReference: (claimId: string, nodeId: string | null) => void;
}) {
  const { t } = useTranslation();
  const [localMode, setMode] = useState<Exclude<Mode, "premises" | "proof"> | null>(null);
  const mode = premiseDialog ? "premises" : proofDialog ? "proof" : localMode;
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();

  useEffect(() => {
    if (mode && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [mode]);

  const show = (next: Exclude<Mode, "proof">) => {
    if (next === "premises") onOpenPremises();
    else setMode(next);
  };
  const researchActions: Exclude<Mode, "theorem" | "proof">[] = ["intake"];
  if (lemmaPlanScope) researchActions.push("lemmas");
  if (premiseReview && (premiseReview.available || premiseReview.proposals.length > 0))
    researchActions.push("premises");
  if (claimCode || claimCodesAvailable) researchActions.push("references");

  return (
    <div className="project-actions">
      <button className="button" type="button" onClick={() => show("theorem")}>
        {t("projectEntry.addTheorem")}
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
        groups={[
          {
            options: researchActions.map((id) => ({ id, label: t(captions[id]), onSelect: () => show(id) })),
          },
        ]}
      />
      <dialog
        ref={dialog}
        className="project-entry"
        aria-labelledby={title}
        onClose={() => {
          setMode(null);
          onClose();
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
