import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { IntakePane } from "./IntakePane";
import { TheoremPane } from "./TheoremPane";
import "../../styles/project-entry.css";

type Mode = "intake" | "theorem";

/** Project-level entry, independent of the selected proof node. */
export function ProjectActions({ session, onRecorded }: { session: string; onRecorded: (claimId?: string) => void }) {
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
      <dialog ref={dialog} className="project-entry" aria-labelledby={title} onClose={() => setMode(null)}>
        {mode && (
          <>
            <header className="project-entry-head">
              <h2 id={title}>{t(mode === "intake" ? "projectEntry.organize" : "projectEntry.addTheorem")}</h2>
              <button className="button" type="button" onClick={() => dialog.current?.close()}>
                {t("projectEntry.close")}
              </button>
            </header>
            <div className="project-entry-body">
              {mode === "intake" ? (
                <IntakePane session={session} onRecorded={onRecorded} />
              ) : (
                <TheoremPane
                  session={session}
                  onRecorded={(claimId) => {
                    if (claimId) dialog.current?.close();
                    onRecorded(claimId);
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
