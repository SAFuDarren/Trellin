import { useTranslation } from "react-i18next";
import type { ReleaseUpdates } from "../api/releaseUpdates";
import { LANGUAGES, setLanguage, type Language } from "../i18n";
import { ModelConnections } from "../components/ModelConnections";
import { ReleaseNotesLink } from "../components/ReleaseUpdateNotice";
import { PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";

export function SettingsPage({
  session,
  onResetLayout,
  updates,
}: {
  session: string;
  onResetLayout: () => void;
  updates: ReleaseUpdates;
}) {
  const { t, i18n } = useTranslation();
  return (
    <SectionLayout
      title={t("rail.settings")}
      groups={[{ entries: [{ key: "general", label: t("settings.general"), href: href("settings"), current: true }] }]}
    >
      <PageHead title={t("settings.general")} />
      <div className="page-body settings">
        <UpdateSettings updates={updates} />
        <ModelConnections session={session} />
        <section aria-labelledby="settings-language">
          <h3 id="settings-language">{t("settings.language")}</h3>
          <p>{t("settings.languageNote")}</p>
          <div className="choices" role="group" aria-labelledby="settings-language">
            {(Object.entries(LANGUAGES) as [Language, string][]).map(([code, name]) => (
              <button
                key={code}
                type="button"
                lang={code}
                className="button"
                aria-pressed={i18n.language === code}
                onClick={() => setLanguage(code)}
              >
                {name}
              </button>
            ))}
          </div>
        </section>
        <section aria-labelledby="settings-layout">
          <h3 id="settings-layout">{t("settings.layout")}</h3>
          <p>{t("settings.layoutNote")}</p>
          <div className="choices">
            <button type="button" className="button" onClick={onResetLayout}>
              {t("settings.resetLayout")}
            </button>
          </div>
        </section>
        <section aria-labelledby="settings-environment">
          <h3 id="settings-environment">{t("settings.environment")}</h3>
          <p>{t("settings.environmentNote")}</p>
          <div className="choices">
            <a className="button" href={href("team", "runtimes")}>
              {t("settings.openRuntimes")}
            </a>
          </div>
        </section>
      </div>
    </SectionLayout>
  );
}

function UpdateSettings({ updates: { state, pending, error, submit } }: { updates: ReleaseUpdates }) {
  const { t, i18n } = useTranslation();
  if (!state) return null;
  const release = state.available;
  const checking = pending === "check" || pending === "auto_check";
  const status = error ? "requestFailed" : checking ? "checking" : `status.${state.status}`;
  return (
    <section aria-labelledby="settings-updates">
      <h3 id="settings-updates">{t("settings.updates.title")}</h3>
      <p>
        {state.installed_version
          ? t("settings.updates.installed", { version: state.installed_version })
          : t("settings.updates.installedUnknown")}
      </p>
      <div className="choices">
        <button
          type="button"
          className="button"
          disabled={Boolean(pending)}
          onClick={() => submit({ action: "check" })}
        >
          {t("settings.updates.check")}
        </button>
        {release && <ReleaseNotesLink url={release.url} />}
      </div>
      <label className="update-preference">
        <input
          type="checkbox"
          checked={state.automatic}
          disabled={Boolean(pending)}
          onChange={(event) => submit({ action: "automatic", enabled: event.currentTarget.checked })}
        />
        {t("settings.updates.automatic")}
      </label>
      <p>{t("settings.updates.automaticNote")}</p>
      <p role="status">{t(`settings.updates.${status}`, { version: release?.version })}</p>
      {state.last_checked_at !== null && (
        <p>
          {t("settings.updates.lastChecked", {
            time: new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(
              state.last_checked_at * 1000,
            ),
          })}
        </p>
      )}
    </section>
  );
}
