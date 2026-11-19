import { useTranslation } from "react-i18next";
import type { ReleaseUpdates } from "../api/releaseUpdates";

export function ReleaseNotesLink({ url }: { url: string }) {
  const { t } = useTranslation();
  return (
    <a className="button" href={url} target="_blank" rel="noopener noreferrer">
      {t("settings.updates.notes")}
    </a>
  );
}

export function ReleaseUpdateNotice({ updates }: { updates: ReleaseUpdates }) {
  const { t } = useTranslation();
  const release = updates.state?.available;
  if (!release || release.dismissed) return null;
  return (
    <aside className="page-notice release-update-notice" aria-label={t("settings.updates.title")}>
      <p role="status">{t("settings.updates.status.available", { version: release.version })}</p>
      <div className="choices">
        <ReleaseNotesLink url={release.url} />
        <button
          type="button"
          className="button"
          disabled={Boolean(updates.pending)}
          onClick={() => updates.submit({ action: "dismiss", version: release.version })}
        >
          {t("settings.updates.dismiss")}
        </button>
      </div>
      {updates.error && <p role="alert">{t("settings.updates.requestFailed")}</p>}
    </aside>
  );
}
