import { createContext, useContext, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { currentEntry, currentRoute, entryHref, SPACES, type SpaceId } from "../shell/spaces";

export type NavEntry = {
  key: string;
  label: string;
  href: string;
  current?: boolean;
  count?: string | number;
  countTone?: "good" | "live";
  sub?: string;
  dot?: "ok" | "warn" | "bad" | "live" | "none";
};

/** Counts shown beside space entries, keyed by entry (inbox, review). The shell provides them. */
export const SpaceCounts = createContext<Record<string, number | undefined>>({});

/** The second level of a space (#289): its entries come first in the panel, then the page's own groups. */
export type NavGroup = { name?: string; entries: NavEntry[]; empty?: string; className?: string };

export function useSpaceGroup(space: SpaceId | undefined): NavGroup | undefined {
  const { t } = useTranslation();
  const definition = SPACES.find((candidate) => candidate.id === space);
  if (!definition || definition.entries.length < 2) return undefined;
  const current = currentEntry(currentRoute())?.key;
  return {
    className: "space-entries",
    entries: definition.entries.map((entry) => ({
      key: `space-${entry.key}`,
      label: t(entry.labelKey),
      href: entryHref(entry),
      current: entry.key === current,
    })),
  };
}

export function SectionLayout({
  title,
  space,
  groups,
  headerActions,
  children,
}: {
  title: string;
  /** The space this page belongs to. Its name heads the panel and its entries come first. */
  space?: SpaceId;
  groups: { name?: string; entries: NavEntry[]; empty?: string }[];
  headerActions?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const spaceGroup = useSpaceGroup(space);
  const spaceCounts = useContext(SpaceCounts);
  const definition = SPACES.find((candidate) => candidate.id === space);
  const heading = definition ? t(definition.labelKey) : title;
  if (spaceGroup) {
    for (const entry of spaceGroup.entries) {
      const count = spaceCounts[entry.key.replace(/^space-/, "")];
      if (count) entry.count = count;
    }
  }
  // A page entry that leads where a space entry already leads (its "All" filter, say) is the same place.
  const spaceTargets = new Set(spaceGroup?.entries.map((entry) => entry.href));
  const pageGroups = groups.map((group, index) => ({
    ...group,
    name: spaceGroup && index === 0 && !group.name ? title : group.name,
    entries: group.entries.filter((entry) => !spaceTargets.has(entry.href)),
  }));
  const allGroups: NavGroup[] = spaceGroup ? [spaceGroup, ...pageGroups] : groups;
  return (
    <div className="section">
      <nav className="section-nav" aria-labelledby="section-title">
        <header className="section-nav-header">
          <h1 id="section-title">{heading}</h1>
          {headerActions}
        </header>
        {allGroups
          .filter((group) => group.entries.length || group.empty)
          .map((group, position) => (
            <div
              className={group.className ? `nav-group ${group.className}` : "nav-group"}
              key={group.name ?? position}
            >
              {group.name && <span>{group.name}</span>}
              {group.entries.length === 0 && group.empty && <p className="nav-empty">{group.empty}</p>}
              {group.entries.map((entry) => (
                <a
                  key={entry.key}
                  className="nav-item"
                  href={entry.href}
                  title={entry.sub ? `${entry.label}\n${entry.sub}` : entry.label}
                  aria-current={entry.current ? "page" : undefined}
                >
                  <span className="nav-line">
                    {entry.dot === "live" ? (
                      <span className="status-dot" data-tone="live" aria-hidden="true" />
                    ) : entry.dot && entry.dot !== "none" ? (
                      <span className="status-dot" data-tone={entry.dot} aria-hidden="true" />
                    ) : null}
                    <span className="label">{entry.label}</span>
                    {entry.count !== undefined && entry.count !== "" && (
                      <span className="count" data-tone={entry.countTone}>
                        {entry.count}
                      </span>
                    )}
                  </span>
                  {entry.sub && <span className="nav-sub">{entry.sub}</span>}
                </a>
              ))}
            </div>
          ))}
      </nav>
      <main className="section-main" tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}

export function PageHead({ title, note, actions }: { title: string; note?: string; actions?: ReactNode }) {
  return (
    <header className="page-head">
      <div className="heading">
        <h2>{title}</h2>
        {note && (
          <span className="note" title={note}>
            {note}
          </span>
        )}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="page-empty">{children}</p>;
}
