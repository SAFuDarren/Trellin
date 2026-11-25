import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Menu, type FloatingGroup } from "./FloatingControls";
import { WorkbenchIcon } from "./Icons";
import { statusText } from "../workbench/text";
import "../styles/research-board.css";

// #326: one row of controls above the board. Filters pick which claims show; Display picks how they are laid out.

const LAYOUTS = ["lanes", "board", "list", "table"] as const;
const DENSITIES = ["comfortable", "compact"] as const;
const PROVED = ["cards", "rows"] as const;
const PER_CELL = [3, 5, 0] as const;

export type BoardLayout = (typeof LAYOUTS)[number];
export type BoardDisplay = {
  layout: BoardLayout;
  density: (typeof DENSITIES)[number];
  proved: (typeof PROVED)[number];
  perCell: (typeof PER_CELL)[number];
};

export const DEFAULT_DISPLAY: BoardDisplay = { layout: "lanes", density: "compact", proved: "rows", perCell: 3 };
const DISPLAY_KEY = "trellin.claims.display";

function readDisplay(): BoardDisplay {
  try {
    const saved = JSON.parse(window.localStorage.getItem(DISPLAY_KEY) ?? "{}") as Partial<BoardDisplay>;
    return {
      layout: LAYOUTS.find((value) => value === saved.layout) ?? DEFAULT_DISPLAY.layout,
      density: DENSITIES.find((value) => value === saved.density) ?? DEFAULT_DISPLAY.density,
      proved: PROVED.find((value) => value === saved.proved) ?? DEFAULT_DISPLAY.proved,
      perCell: PER_CELL.find((value) => value === saved.perCell) ?? DEFAULT_DISPLAY.perCell,
    };
  } catch {
    return DEFAULT_DISPLAY;
  }
}

export function useBoardDisplay() {
  const [display, setDisplay] = useState(readDisplay);
  function change(next: BoardDisplay) {
    setDisplay(next);
    try {
      window.localStorage.setItem(DISPLAY_KEY, JSON.stringify(next));
    } catch {
      // The choice still applies to this window when preference storage is unavailable.
    }
  }
  return [display, change] as const;
}

/** Domains collapsed on the board, kept across visits. */
export function useCollapsedLanes() {
  const key = "trellin.claims.collapsed";
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => {
    try {
      const saved = JSON.parse(window.localStorage.getItem(key) ?? "[]");
      return new Set(Array.isArray(saved) ? saved.filter((item): item is string => typeof item === "string") : []);
    } catch {
      return new Set();
    }
  });
  function toggle(lane: string) {
    const next = new Set(collapsed);
    if (next.has(lane)) next.delete(lane);
    else next.add(lane);
    setCollapsed(next);
    try {
      window.localStorage.setItem(key, JSON.stringify([...next]));
    } catch {
      // Collapsing still applies to this window.
    }
  }
  return [collapsed, toggle] as const;
}

export type BoardFilters = {
  domain: string;
  domains: string[];
  statuses: ReadonlySet<string>;
  allStatuses: string[];
  search: string;
};

export function BoardControls({
  filters,
  display,
  onDomain,
  onToggleStatus,
  onSearch,
  onDisplay,
  onNew,
}: {
  filters: BoardFilters;
  display: BoardDisplay;
  onDomain: (domain: string) => void;
  onToggleStatus: (status: string) => void;
  onSearch: (text: string) => void;
  onDisplay: (display: BoardDisplay) => void;
  onNew?: () => void;
}) {
  const { t } = useTranslation();
  const [searching, setSearching] = useState(Boolean(filters.search));
  const active = Number(Boolean(filters.domain)) + filters.statuses.size;

  const filterGroups: FloatingGroup[] = [
    {
      label: t("researchBoard.filter.domain"),
      options: [
        {
          id: "domain:",
          label: t("researchBoard.filter.anyDomain"),
          current: !filters.domain,
          onSelect: () => onDomain(""),
        },
        ...filters.domains.map((domain) => ({
          id: "domain:" + domain,
          label: domain,
          current: filters.domain === domain,
          onSelect: () => onDomain(domain),
        })),
      ],
    },
    {
      label: t("researchBoard.filter.status"),
      multiple: true,
      options: filters.allStatuses.map((status) => ({
        id: "status:" + status,
        label: statusText(t, status),
        current: filters.statuses.has(status),
        onSelect: () => onToggleStatus(status),
      })),
    },
  ];

  const displayGroups: FloatingGroup[] = [
    {
      label: t("researchBoard.display.layout"),
      options: LAYOUTS.map((layout) => ({
        id: "layout:" + layout,
        label: t(`researchBoard.display.layouts.${layout}`),
        current: display.layout === layout,
        onSelect: () => onDisplay({ ...display, layout }),
      })),
    },
    {
      label: t("researchBoard.display.density"),
      options: DENSITIES.map((density) => ({
        id: "density:" + density,
        label: t(`researchBoard.display.densities.${density}`),
        current: display.density === density,
        onSelect: () => onDisplay({ ...display, density }),
      })),
    },
    {
      label: t("researchBoard.display.proved"),
      options: PROVED.map((proved) => ({
        id: "proved:" + proved,
        label: t(`researchBoard.display.provedAs.${proved}`),
        current: display.proved === proved,
        onSelect: () => onDisplay({ ...display, proved }),
      })),
    },
    {
      label: t("researchBoard.display.perCell"),
      options: PER_CELL.map((perCell) => ({
        id: "per-cell:" + perCell,
        label: perCell ? t("researchBoard.display.cards", { count: perCell }) : t("researchBoard.display.allCards"),
        current: display.perCell === perCell,
        onSelect: () => onDisplay({ ...display, perCell }),
      })),
    },
    {
      options: [{ id: "reset", label: t("researchBoard.display.reset"), onSelect: () => onDisplay(DEFAULT_DISPLAY) }],
    },
  ];

  return (
    <div className="board-controls">
      {searching ? (
        <label className="board-search">
          <WorkbenchIcon name="search" />
          <span className="sr-only">{t("researchBoard.search")}</span>
          <input
            type="search"
            value={filters.search}
            placeholder={t("researchBoard.searchPlaceholder")}
            autoFocus
            onChange={(event) => onSearch(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Escape") return;
              onSearch("");
              setSearching(false);
            }}
            onBlur={() => {
              if (!filters.search) setSearching(false);
            }}
          />
        </label>
      ) : (
        <button
          type="button"
          className="icon-button"
          aria-label={t("researchBoard.search")}
          title={t("researchBoard.search")}
          onClick={() => setSearching(true)}
        >
          <WorkbenchIcon name="search" />
        </button>
      )}
      <Menu
        label={active ? t("researchBoard.filter.active", { count: active }) : t("researchBoard.filter.label")}
        triggerClassName="button board-control"
        dataCurrent={active > 0}
        placement="bottom-end"
        trigger={
          <>
            <WorkbenchIcon name="filter" />
            {active ? t("researchBoard.filter.active", { count: active }) : t("researchBoard.filter.label")}
          </>
        }
        groups={filterGroups}
      />
      <Menu
        label={t("researchBoard.display.label")}
        triggerClassName="button board-control"
        placement="bottom-end"
        trigger={
          <>
            <WorkbenchIcon name="display" />
            {t("researchBoard.display.label")}
          </>
        }
        groups={displayGroups}
      />
      {onNew && (
        <button type="button" className="button primary" onClick={onNew}>
          <WorkbenchIcon name="add" />
          {t("researchBoard.newClaim")}
        </button>
      )}
    </div>
  );
}

/** Each active condition as a removable chip; nothing renders while no filter is set. */
export function BoardFilterChips({
  filters,
  onDomain,
  onToggleStatus,
  onSearch,
  onClear,
}: {
  filters: BoardFilters;
  onDomain: (domain: string) => void;
  onToggleStatus: (status: string) => void;
  onSearch: (text: string) => void;
  onClear: () => void;
}) {
  const { t } = useTranslation();
  const chips = [
    ...(filters.search.trim()
      ? [
          {
            id: "search",
            label: t("researchBoard.chips.search", { text: filters.search.trim() }),
            remove: () => onSearch(""),
          },
        ]
      : []),
    ...(filters.domain
      ? [
          {
            id: "domain",
            label: t("researchBoard.chips.domain", { domain: filters.domain }),
            remove: () => onDomain(""),
          },
        ]
      : []),
    ...[...filters.statuses].map((status) => ({
      id: "status:" + status,
      label: t("researchBoard.chips.status", { status: statusText(t, status) }),
      remove: () => onToggleStatus(status),
    })),
  ];
  if (!chips.length) return null;
  return (
    <div className="board-chips" role="group" aria-label={t("researchBoard.chips.label")}>
      {chips.map((chip) => (
        <span className="board-chip" key={chip.id}>
          {chip.label}
          <button
            type="button"
            aria-label={t("researchBoard.chips.remove", { label: chip.label })}
            onClick={chip.remove}
          >
            <WorkbenchIcon name="cancel" />
          </button>
        </span>
      ))}
      <button type="button" className="link-button" onClick={onClear}>
        {t("researchBoard.chips.clear")}
      </button>
    </div>
  );
}
