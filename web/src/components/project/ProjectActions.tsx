import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ClaimCodesPane } from "./ClaimCodesPane";
import { IntakePane } from "./IntakePane";
import { ProofTaskPane } from "./ProofTaskPane";
import { TheoremPane } from "./TheoremPane";
import "../../styles/project-entry.css";

const captions = {
  intake: "projectEntry.organize",
  theorem: "projectEntry.addTheorem",
  proof: "proofTask.title",
  references: "claimCodes.title",
} as const;

type Mode = keyof typeof captions;

/** Project actions and proof work scoped to the selected claim. */
export function ProjectActions({
  session,
  claimId,
  claimCode,
  claimCodesAvailable,
  onRecorded,
  onGoalRecorded,
  onOpenReference,
}: {
  session: string;
  claimId: string | null;
  claimCode?: string | null;
  claimCodesAvailable: boolean;
  onRecorded: (claimId?: string) => void | Promise<void>;
  onGoalRecorded: (claimId: string) => void | Promise<void>;
  onOpenReference: (claimId: string, nodeId: string | null) => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<Mode | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const title = useId();

  useEffect(() => {
    if (mode && dialog.current && !dialog.current.open) dialog.current.showModal();
  }, [mode]);

  return (
    <div className="project-actions">
      <button className="button" type="button" onClick={() => setMode("intake")}>
        {t("projectEntry.organize")}
      </button>
      <button className="button" type="button" onClick={() => setMode("theorem")}>
        {t("projectEntry.addTheorem")}
      </button>
      {claimId && (
        <button className="button" type="button" onClick={() => setMode("proof")}>
          {t("proofTask.title")}
        </button>
      )}
      {(claimCode || claimCodesAvailable) && (
        <button className="button" type="button" onClick={() => setMode("references")}>
          {t("claimCodes.title")}
        </button>
      )}
      <dialog ref={dialog} className="project-entry" aria-labelledby={title} onClose={() => setMode(null)}>
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
