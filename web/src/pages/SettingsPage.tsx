import { useTranslation } from "react-i18next";
import { LANGUAGES, setLanguage, type Language } from "../i18n";
import { PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";

export function SettingsPage({ onResetLayout }: { onResetLayout: () => void }) {
  const { t, i18n } = useTranslation();
  return (
    <SectionLayout
      title={t("rail.settings")}
      groups={[{ entries: [{ key: "general", label: t("settings.general"), href: href("settings"), current: true }] }]}
    >
      <PageHead title={t("settings.general")} />
      <div className="page-body settings">
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
