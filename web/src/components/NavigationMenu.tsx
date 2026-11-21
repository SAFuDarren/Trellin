import type { ReactNode } from "react";
import { Menu } from "./FloatingControls";
import "../styles/navigation-menu.css";

type NavigationItem = { href: string; label: string; current?: boolean; badge?: number };

/** A shared anchored menu whose page choices retain their navigation targets. */
export function NavigationMenu({
  label,
  className,
  indicator,
  children,
  items,
}: {
  label: string;
  className?: string;
  indicator?: boolean;
  children: ReactNode;
  items: NavigationItem[];
}) {
  return (
    <Menu
      label={label}
      triggerClassName={className ? `navigation-trigger ${className}` : "navigation-trigger"}
      className="navigation-menu"
      dataCurrent={indicator}
      trigger={children}
      groups={[
        {
          options: items.map((item) => ({
            id: item.href,
            href: item.href,
            label: item.label,
            current: item.current,
            count: item.badge,
          })),
        },
      ]}
    />
  );
}
