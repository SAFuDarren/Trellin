import { useTranslation } from "react-i18next";
import { PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";

const KEYS = ["drag", "zoom", "keys", "fit", "borders", "select"] as const;
const GUIDES = ["workbench", "quickstart", "review", "progress"] as const;

export function HelpPage() {
  const { t } = useTranslation();
  return (
    <SectionLayout
      title={t("rail.help")}
      groups={[{ entries: [{ key: "basics", label: t("help.basics"), href: href("help"), current: true }] }]}
    >
      <PageHead title={t("help.basics")} />
      <div className="page-body help">
        <p className="page-note">{t("help.intro")}</p>
        <section className="block">
          <h3>{t("help.sections")}</h3>
          <dl className="keys">
            {(["workbench", "review", "claims", "library", "chat", "team", "progress"] as const).map((section) => (
              <span key={section} className="contents">
                <dt>
                  <a className="relation" href={href(section)}>
                    {t(`rail.${section}`)}
                  </a>
                </dt>
                <dd>{t(`help.about.${section}`)}</dd>
              </span>
            ))}
          </dl>
        </section>
        <section className="block">
          <h3>{t("help.canvas")}</h3>
          <dl className="keys">
            {KEYS.map((key) => (
              <span key={key} className="contents">
                <dt>
                  <kbd>{t(`help.keys.${key}.key`)}</kbd>
                </dt>
                <dd>{t(`help.keys.${key}.what`)}</dd>
              </span>
            ))}
          </dl>
        </section>
        <section className="block">
          <h3>{t("help.guides")}</h3>
          <ul>
            {GUIDES.map((guide) => (
              <li key={guide}>{t(`help.docs.${guide}`)}</li>
            ))}
          </ul>
        </section>
      </div>
    </SectionLayout>
  );
}
