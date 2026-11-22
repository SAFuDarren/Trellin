import { createContext, useContext, useState, type CSSProperties, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { currentEntry, currentRoute, entryHref, SPACES, type SpaceId } from "../shell/spaces";
import { clampSize, useLayout } from "../workbench/useLayout";
import { Splitter } from "./Splitter";

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

function useSpaceGroup(space: SpaceId | undefined): NavGroup | undefined {
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

export function SectionPanel({
  title,
  actions,
  children,
}: {
  title: string;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <nav className="section-nav" aria-labelledby="section-title">
      <header className="section-nav-header">
        <h1 id="section-title">{title}</h1>
        {actions}
      </header>
      {children}
    </nav>
  );
}

export function SectionFrame({
  panel,
  children,
  chatThread,
}: {
  panel: ReactNode;
  children: ReactNode;
  chatThread?: boolean;
}) {
  const { t } = useTranslation();
  const layout = useLayout();
  const [dragWidth, setDragWidth] = useState<number>();
  const side = dragWidth ?? layout.clamped.side;
  const chat = chatThread !== undefined;
  return (
    <div
      className={chat ? "section chats-page" : "section"}
      data-thread={chatThread}
      style={{ "--side-w": `${side}px` } as CSSProperties}
    >
      {panel}
      <Splitter
        name="side"
        value={side}
        label={t("splitter.side")}
        onResize={(width) => setDragWidth(clampSize("side", width, layout.windowHeight))}
        onCommit={(width) => {
          setDragWidth(undefined);
          layout.update({ side: clampSize("side", width, layout.windowHeight) });
        }}
      />
      <main className={chat ? "section-main chats-main" : "section-main"} tabIndex={-1}>
        {children}
      </main>
    </div>
  );
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
  const panel = (
    <SectionPanel title={heading} actions={headerActions}>
      {allGroups
        .filter((group) => group.entries.length || group.empty)
        .map((group, position) => (
          <div className={group.className ? `nav-group ${group.className}` : "nav-group"} key={group.name ?? position}>
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
    </SectionPanel>
  );
  return <SectionFrame panel={panel}>{children}</SectionFrame>;
}

export function PageHead({
  title,
  heading,
  note,
  actions,
  className,
}: ({ title: string; heading?: never } | { heading: ReactNode; title?: never }) & {
  note?: string;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={className ? `page-head ${className}` : "page-head"}>
      <div className="heading">
        {heading ?? <h2 title={title}>{title}</h2>}
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
