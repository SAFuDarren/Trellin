import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { href, type Section } from "../shell/route";
import { NavigationMenu } from "./NavigationMenu";

const ICONS: Record<string, string> = {
  search: "M20 20l-4.2-4.2M17 10.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0Z",
  workbench: "M3.5 10.5 12 3.5l8.5 7M5.5 9v10.5a1 1 0 0 0 1 1h3.25v-6h4.5v6h3.25a1 1 0 0 0 1-1V9",
  claims:
    "M5 4.5h14a1.5 1.5 0 0 1 1.5 1.5v12a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5V6A1.5 1.5 0 0 1 5 4.5ZM9.5 4.5v15M15 4.5v15",
  library: "M5 4.5h4.5v15H5zM9.5 4.5H14v15H9.5zM14.5 5.2l3.8-1 3.2 14.3-3.8 1z",
  chat: "M5 5h14a1.5 1.5 0 0 1 1.5 1.5v9A1.5 1.5 0 0 1 19 17h-8l-4 3v-3H5a1.5 1.5 0 0 1-1.5-1.5v-9A1.5 1.5 0 0 1 5 5Z",
  team: "M9 11a3.5 3.5 0 1 0 0-7a3.5 3.5 0 0 0 0 7ZM2.5 20c.6-3.3 3.2-5.5 6.5-5.5s5.9 2.2 6.5 5.5M16 4.5a3.5 3.5 0 0 1 0 6.5M18 14.8c1.9.7 3.2 2.5 3.5 5.2",
  progress: "M20.5 12A8.5 8.5 0 1 1 12 3.5M13 7.5l-3 5h4l-3 5M16.5 3.5H20.5V7.5",
  settings:
    "M9 12a3 3 0 1 0 6 0a3 3 0 1 0 -6 0M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8",
};

const GROUPS: Section[][] = [
  ["workbench", "claims", "library"],
  ["chat", "team", "progress"],
];
const UTILITIES = ["settings", "help"] as const;
const brandMark = new URL("../brand/favicon.svg", import.meta.url).href;
const HOVER_DELAY = 300;
const SWITCH_WINDOW = 450;
type Hint = { label: string; x: number; y: number };

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
  onSearch,
}: {
  current: Section;
  badges: Partial<Record<Section, { count: number; live?: boolean }>>;
  onSearch: () => void;
}) {
  const { t } = useTranslation();
  const [hint, setHint] = useState<Hint | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const warmUntil = useRef(0);

  const hide = () => {
    window.clearTimeout(timer.current);
    setHint(null);
  };
  const show = (anchor: HTMLAnchorElement, label: string, delay: number) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      const bounds = anchor.getBoundingClientRect();
      setHint({ label, x: bounds.right + 11, y: bounds.top + bounds.height / 2 });
    }, delay);
  };

  useEffect(() => {
    const dismiss = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      window.clearTimeout(timer.current);
      setHint(null);
    };
    window.addEventListener("keydown", dismiss);
    return () => {
      window.removeEventListener("keydown", dismiss);
      window.clearTimeout(timer.current);
    };
  }, []);

  const link = (section: Section) => {
    const label = t(section === "workbench" ? "navigation.home" : `rail.${section}`);
    const badge = badges[section];
    const badgeLabel = badge?.count ? t(`rail.badge.${section}`, { count: badge.count }) : "";
    return (
      <li key={section}>
        <a
          className="rail-link"
          href={href(section)}
          aria-label={badgeLabel ? `${label} · ${badgeLabel}` : label}
          aria-current={current === section ? "page" : undefined}
          onPointerEnter={(event) => {
            if (event.pointerType === "mouse")
              show(event.currentTarget, label, performance.now() < warmUntil.current ? 0 : HOVER_DELAY);
          }}
          onPointerLeave={() => {
            if (hint) warmUntil.current = performance.now() + SWITCH_WINDOW;
            hide();
          }}
          onPointerDown={hide}
          onFocus={(event) => {
            if (event.currentTarget.matches(":focus-visible")) show(event.currentTarget, label, 0);
          }}
          onBlur={hide}
        >
          <Icon name={section} />
          {badge && badge.count > 0 && (
            <b className={badge.live ? "rail-badge live" : "rail-badge"} aria-hidden="true">
              {badge.count > 99 ? "99+" : badge.count}
            </b>
          )}
        </a>
      </li>
    );
  };

  return (
    <nav className="rail" aria-label={t("rail.label")}>
      <a className="rail-brand" href={href("workbench")} title={t("app.name")} aria-label={t("app.name")}>
        <img src={brandMark} alt="" width={32} height={32} />
      </a>
      <div className="rail-main" onScroll={hide}>
        {GROUPS.map((sections) => (
          <ul className="rail-group" key={sections[0]}>
            {sections.map(link)}
          </ul>
        ))}
      </div>
      <div className="rail-foot">
        <button
          className="rail-link"
          type="button"
          aria-label={t("palette.title")}
          aria-haspopup="dialog"
          aria-keyshortcuts="Meta+K Control+K"
          title={t("palette.title")}
          onClick={onSearch}
        >
          <Icon name="search" />
        </button>
        <NavigationMenu
          label={t("navigation.utilities")}
          className="rail-link"
          indicator={current === "settings" || current === "help"}
          items={UTILITIES.map((section) => ({
            href: href(section),
            label: t(`rail.${section}`),
            current: current === section,
          }))}
        >
          <Icon name="settings" />
        </NavigationMenu>
      </div>
      {hint &&
        createPortal(
          <span className="rail-hint" role="tooltip" style={{ left: hint.x, top: hint.y }}>
            {hint.label}
          </span>,
          document.body,
        )}
    </nav>
  );
}
