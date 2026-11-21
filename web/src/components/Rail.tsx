import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { href, type Section } from "../shell/route";
import { WorkbenchIcon } from "./Icons";
import { NavigationMenu } from "./NavigationMenu";

const GROUPS: Section[][] = [
  ["workbench", "claims", "library"],
  ["chat", "team", "progress"],
];
const UTILITIES = ["settings", "help"] as const;
const brandMark = new URL("../brand/favicon.svg", import.meta.url).href;
const HOVER_DELAY = 300;
const SWITCH_WINDOW = 450;
type Hint = { label: string; x: number; y: number };

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
          <WorkbenchIcon name={section} />
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
          <WorkbenchIcon name="search" />
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
          <WorkbenchIcon name="settings" />
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
