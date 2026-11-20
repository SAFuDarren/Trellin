import { useTranslation } from "react-i18next";
import { href, type Section } from "../shell/route";

const ICONS: Record<string, string> = {
  workbench:
    "M3.5 6a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0M15.5 6a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0M9.5 18a2.5 2.5 0 1 0 5 0a2.5 2.5 0 1 0 -5 0M7.3 8.2l3.6 7.6M16.7 8.2l-3.6 7.6",
  review: "M4 13h4.5l1.5 2.5h4l1.5-2.5H20M5.5 5h13L20 13v5.5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V13Z",
  claims:
    "M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 5 4.5ZM9.5 4.5v15M15 4.5v15",
  library: "M5 4.5h4.5v15H5zM9.5 4.5H14v15H9.5zM14.5 5.2l3.8-1 3.2 14.3-3.8 1z",
  chat: "M5 5h14a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 17h-8l-4 3v-3H5a1.5 1.5 0 0 1-1.5-1.5v-9A1.5 1.5 0 0 1 5 5Z",
  team: "M9 11a3.5 3.5 0 1 0 0-7a3.5 3.5 0 0 0 0 7ZM2.5 20c.6-3.3 3.2-5.5 6.5-5.5s5.9 2.2 6.5 5.5M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14.8c1.9.7 3.2 2.5 3.5 5.2",
  progress: "M20.5 12A8.5 8.5 0 1 1 12 3.5M13 7.5l-3 5h4l-3 5M16.5 3.5H20.5V7.5",
  help: "M3.5 12a8.5 8.5 0 1 0 17 0a8.5 8.5 0 1 0 -17 0M9.8 9.5a2.3 2.3 0 0 1 4.4.9c0 1.6-2.2 2-2.2 3.3M12 16.5h.01",
  settings:
    "M9 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8",
};

const MAIN: Section[] = ["workbench", "review", "claims", "library", "chat", "team", "progress"];

export function Icon({ name }: { name: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[name] ?? ""} />
    </svg>
  );
}

export function Rail({
  current,
  badges,
}: {
  current: Section;
  badges: Partial<Record<Section, { count: number; live?: boolean }>>;
}) {
  const { t } = useTranslation();
  return (
    <nav className="rail" aria-label={t("rail.label")}>
      <a className="rail-brand" href={href("workbench")} title={t("app.name")} aria-label={t("app.name")}>
        R
      </a>
      {MAIN.map((section) => {
        const badge = badges[section];
        return (
          <a key={section} href={href(section)} aria-current={current === section ? "page" : undefined}>
            <Icon name={section} />
            <span>{t(`rail.${section}`)}</span>
            {badge && badge.count > 0 && (
              <b
                className={badge.live ? "rail-badge live" : "rail-badge"}
                aria-label={t(`rail.badge.${section}`, { count: badge.count })}
              >
                {badge.count}
              </b>
            )}
          </a>
        );
      })}
      <div className="rail-foot">
        {(["help", "settings"] as const).map((section) => (
          <a
            key={section}
            href={href(section)}
            className="rail-icon"
            title={t(`rail.${section}`)}
            aria-label={t(`rail.${section}`)}
            aria-current={current === section ? "page" : undefined}
          >
            <Icon name={section} />
          </a>
        ))}
      </div>
    </nav>
  );
}
