import { useEffect, useId, useRef } from "react";
import { useTranslation } from "react-i18next";
import { graphAction, graphDialogTitle, type ProjectAction, type ProjectDialog } from "../../workbench/actions";
import type { PremiseReview } from "../../workbench/types";
import { Menu } from "../FloatingControls";
import { IconHint } from "../IconHint";
import { WorkbenchIcon } from "../Icons";
import { ClaimCodesPane } from "./ClaimCodesPane";
import { IntakePane } from "./IntakePane";
import { LemmaPlanPane, type LemmaPlanScope } from "./LemmaPlanPane";
import { PremisePane } from "./PremisePane";
import { ProofTaskPane } from "./ProofTaskPane";
import { TheoremPane } from "./TheoremPane";
import "../../styles/project-entry.css";

type Mode = ProjectDialog | "premises";

/**
 * #353: the project map's one Add menu, and the dialogs it and a claim's context menu open.
 * Claim-scoped work (lemmas, premises, proof task) is reached from the claim, not from here.
 */
export function ProjectActions({
  session,
  claimId,
  claimCode,
  lemmaPlanScope,
  claimTitle,
  premiseReview,
  premiseDialog,
  projectAction,
  importFile,
  onProjectAction,
  onClose,
  onPremiseRecorded,
  onRecorded,
  onGoalRecorded,
  onOpenReference,
}: {
  session: string;
  claimId: string | null;
  claimCode?: string | null;
  lemmaPlanScope: LemmaPlanScope | null;
  claimTitle: string;
  premiseReview?: PremiseReview;
  premiseDialog: { id?: string } | null;
  projectAction: ProjectDialog | null;
  /** A file dropped on the map or chosen from its empty state, read into the import. */
  importFile?: File | null;
  onProjectAction: (id: ProjectAction) => void;
  onClose: () => void;
  onPremiseRecorded: () => Promise<void>;
  onRecorded: (claimId?: string) => void | Promise<void>;
  onGoalRecorded: (claimId: string) => void | Promise<void>;
  onOpenReference: (claimId: string, nodeId: string | null) => void;
}) {
  const { t } = useTranslation();
  const mode: Mode | null = premiseDialog ? "premises" : projectAction;
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();

  useEffect(() => {
    if (mode && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [mode]);

  const addOptions = [
    {
      ...graphAction(t, "organize-research", () => onProjectAction("organize-research")),
      detail: t("projectEntry.dropHint"),
    },
    graphAction(t, "add-theorem", () => onProjectAction("add-theorem")),
  ];

  return (
    <div className="project-actions">
      <IconHint label={t("projectEntry.addTo")}>
        <Menu
          label={t("projectEntry.addTo")}
          trigger={<WorkbenchIcon name="add" />}
          triggerClassName="button map-view-toggle project-add-trigger"
          className="workbench-actions-items"
          placement="bottom-end"
          groups={[{ options: addOptions }]}
        />
      </IconHint>
      <dialog ref={dialog} className="project-entry" aria-labelledby={title} onClose={onClose}>
        {mode && (
          <>
            <header className="project-entry-head">
              <h2 id={title}>{graphDialogTitle(t, mode)}</h2>
              <button className="button" type="button" onClick={() => dialog.current?.close()}>
                {t("projectEntry.close")}
              </button>
            </header>
            <div className="project-entry-body">
              {mode === "organize-research" && (
                <IntakePane
                  key={importFileKey(importFile)}
                  session={session}
                  file={importFile}
                  onRecorded={onRecorded}
                />
              )}
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
              {mode === "lemma-plan" &&
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

const fileKeys = new WeakMap<File, string>();
let fileCount = 0;
/** Remount the import for each new file so its title and text start from that file. */
function importFileKey(file: File | null | undefined) {
  if (!file) return "notes";
  let key = fileKeys.get(file);
  if (!key) fileKeys.set(file, (key = `file-${++fileCount}`));
  return key;
}
