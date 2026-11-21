import { useTranslation } from "react-i18next";
import { PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { formatShortcut, SHORTCUTS } from "../shortcuts";

const KEYS = ["palette", "drag", "zoom", "keys", "move", "fit", "borders", "select"] as const;
const GUIDES = ["workbench", "quickstart", "review", "progress"] as const;

export function HelpPage({ onShowShortcuts }: { onShowShortcuts: () => void }) {
  const { t } = useTranslation();
  return (
    <SectionLayout
      title={t("rail.help")}
      groups={[{ entries: [{ key: "basics", label: t("help.basics"), href: href("help"), current: true }] }]}
    >
      <PageHead
        title={t("help.basics")}
        actions={
          <button
            type="button"
            className="button shortcut-sheet-launcher"
            aria-haspopup="dialog"
            aria-keyshortcuts={SHORTCUTS.sheet}
            onClick={onShowShortcuts}
          >
            {t("shortcuts.title")}
            <kbd aria-hidden="true">{formatShortcut(SHORTCUTS.sheet)}</kbd>
          </button>
        }
      />
      <div className="page-body help">
        <p className="page-note">{t("help.intro")}</p>
        <section className="block">
          <h3>{t("help.sections")}</h3>
          <dl className="keys">
            {(["workbench", "review", "claims", "library", "chat", "team", "progress"] as const).map((section) => (
              <div key={section} className="contents">
                <dt>
                  <a className="relation" href={href(section)}>
                    {t(`rail.${section}`)}
                  </a>
                </dt>
                <dd>{t(`help.about.${section}`)}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section className="block">
          <h3>{t("help.canvas")}</h3>
          <dl className="keys">
            {KEYS.map((key) => (
              <div key={key} className="contents">
                <dt>
                  <kbd>
                    {key === "palette"
                      ? formatShortcut(SHORTCUTS.palette)
                      : t(`help.keys.${key}.key`, { shortcut: formatShortcut("Primary") })}
                  </kbd>
                </dt>
                <dd>
                  {t(`help.keys.${key}.what`, {
                    confirm: formatShortcut(SHORTCUTS.enter),
                    cancel: formatShortcut(SHORTCUTS.escape),
                    shift: formatShortcut("Shift"),
                  })}
                </dd>
              </div>
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
