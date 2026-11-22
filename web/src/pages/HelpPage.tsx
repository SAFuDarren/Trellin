import { useTranslation } from "react-i18next";
import { PageHead, SectionLayout } from "../components/SectionLayout";
import { navigationShortcutRows } from "../components/ShortcutSheet";
import { href, type Section } from "../shell/route";
import { formatShortcut, SHORTCUTS } from "../shortcuts";

const PLACES = ["workbench", "claims", "library", "chat", "team", "progress", "settings"] as const;
const STEPS = ["first", "second", "third"] as const;
const GUIDES: readonly {
  id: string;
  links: readonly { section: Section; sub?: string; label: string }[];
}[] = [
  { id: "project-map", links: [{ section: "workbench", label: "help.openMap" }] },
  {
    id: "agents",
    links: [
      { section: "team", sub: "agents", label: "help.openAgents" },
      { section: "agent-work", label: "help.openAgentWork" },
    ],
  },
  {
    id: "proofs",
    links: [
      { section: "claims", label: "help.openClaims" },
      { section: "library", label: "help.openLibrary" },
    ],
  },
];

export function HelpPage({ sub, onShowShortcuts }: { sub: string; onShowShortcuts: () => void }) {
  const { t } = useTranslation();
  const guide = GUIDES.find(({ id }) => id === sub);
  const essentials = navigationShortcutRows(t).filter(({ id }) => id === "palette" || id === "context");
  return (
    <SectionLayout
      title={t("rail.help")}
      groups={[
        { entries: [{ key: "basics", label: t("help.basics"), href: href("help"), current: !guide }] },
        {
          name: t("help.guides"),
          entries: GUIDES.map(({ id }) => ({
            key: id,
            label: t(`help.guide.${id}.title`),
            href: href("help", id),
            current: guide?.id === id,
          })),
        },
      ]}
    >
      <PageHead
        title={guide ? t(`help.guide.${guide.id}.title`) : t("help.basics")}
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
        {guide ? (
          <>
            <ol>
              {STEPS.map((step) => (
                <li key={step}>{t(`help.guide.${guide.id}.${step}`)}</li>
              ))}
            </ol>
            <div className="choices">
              {guide.links.map((link) => (
                <a key={link.label} className="button" href={href(link.section, link.sub)}>
                  {t(link.label)}
                </a>
              ))}
            </div>
          </>
        ) : (
          <>
            <p className="page-note">{t("help.intro", { name: t("app.name") })}</p>
            <section className="block" aria-labelledby="help-places">
              <h3 id="help-places">{t("help.sections")}</h3>
              <dl className="keys">
                {PLACES.map((section) => (
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
            <section className="block" aria-labelledby="help-controls">
              <h3 id="help-controls">{t("help.controls")}</h3>
              <dl className="keys">
                {essentials.map(({ id, label, keys }) => (
                  <div key={id} className="contents">
                    <dt>
                      {keys.map((key) => (
                        <kbd key={key}>{formatShortcut(key)}</kbd>
                      ))}
                    </dt>
                    <dd>{label}</dd>
                  </div>
                ))}
              </dl>
              <p className="page-note">
                {t("help.moveCard", {
                  confirm: formatShortcut(SHORTCUTS.enter),
                  cancel: formatShortcut(SHORTCUTS.escape),
                })}
              </p>
            </section>
          </>
        )}
      </div>
    </SectionLayout>
  );
}
