import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { href, type Section } from "../shell/route";
import { entryHref, SPACES, spaceOf, type Space, type SpaceId } from "../shell/spaces";
import { formatShortcut, SHORTCUTS } from "../shortcuts";
import { WorkbenchIcon } from "./Icons";
import { ProjectTile } from "./SidebarProjects";

const SPACE_ICONS: Record<SpaceId, string> = {
  home: "workbench",
  work: "work",
  chat: "chat",
  team: "team",
  automation: "progress",
};
const UTILITIES = ["help", "settings"] as const;
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
  badges: Partial<Record<SpaceId, { count: number; live?: boolean }>>;
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

  const active = spaceOf(current)?.id;
  const railLink = ({
    id,
    target,
    label,
    icon,
    isCurrent,
    badge,
    badgeLabel,
  }: {
    id: string;
    target: string;
    label: string;
    icon: string;
    isCurrent: boolean;
    badge?: { count: number; live?: boolean };
    badgeLabel?: string;
  }) => (
    <a
      key={id}
      className="rail-link"
      href={target}
      aria-label={badgeLabel ? `${label} · ${badgeLabel}` : label}
      aria-current={isCurrent ? "page" : undefined}
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
      <WorkbenchIcon name={icon} />
      {badge && badge.count > 0 && (
        <b className={badge.live ? "rail-badge live" : "rail-badge"} aria-hidden="true">
          {badge.count > 99 ? "99+" : badge.count}
        </b>
      )}
    </a>
  );
  const link = (space: Space) => {
    const badge = badges[space.id];
    return (
      <li key={space.id}>
        {railLink({
          id: space.id,
          target: entryHref(space.entries[0]!),
          label: t(space.labelKey),
          icon: SPACE_ICONS[space.id],
          isCurrent: active === space.id,
          badge,
          badgeLabel: badge?.count ? t(`rail.badge.${space.id}`, { count: badge.count }) : "",
        })}
      </li>
    );
  };

  return (
    <nav className="rail" aria-label={t("rail.label")}>
      <ProjectTile
        fallback={
          <a className="rail-brand" href={href("workbench")} title={t("app.name")} aria-label={t("app.name")}>
            <img src={brandMark} alt="" width={32} height={32} />
          </a>
        }
      />
      <div className="rail-main" onScroll={hide}>
        <ul className="rail-group">{SPACES.map(link)}</ul>
      </div>
      <div className="rail-foot">
        <button
          className="rail-link"
          type="button"
          aria-label={t("palette.title")}
          aria-haspopup="dialog"
          aria-keyshortcuts={`${SHORTCUTS.palette.replace("Primary", "Meta")} ${SHORTCUTS.palette.replace("Primary", "Control")}`}
          title={t("common.shortcutHint", { label: t("palette.title"), shortcut: formatShortcut(SHORTCUTS.palette) })}
          onClick={onSearch}
        >
          <WorkbenchIcon name="search" />
        </button>
        {/* #293: Help and Settings each open in one click; Settings no longer hides behind a menu. */}
        {UTILITIES.map((section) =>
          railLink({
            id: section,
            target: href(section),
            label: t(`rail.${section}`),
            icon: section,
            isCurrent: current === section,
          }),
        )}
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
