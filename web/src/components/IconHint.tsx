import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { formatShortcut } from "../shortcuts";

/**
 * #353: an icon-only control names itself on hover and keyboard focus, as the map's view toggles do.
 * The control keeps its own accessible name; the hint repeats it visually with its shortcut.
 */
export function IconHint({ label, shortcut, children }: { label: string; shortcut?: string; children: ReactNode }) {
  const { t } = useTranslation();
  return (
    <span className="map-view-control">
      {children}
      <span className="map-view-hint" aria-hidden="true">
        {shortcut ? t("common.shortcutHint", { label, shortcut: formatShortcut(shortcut) }) : label}
      </span>
    </span>
  );
}
