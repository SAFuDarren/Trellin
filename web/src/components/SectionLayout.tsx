import type { ReactNode } from "react";

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

export function SectionLayout({
  title,
  groups,
  children,
}: {
  title: string;
  groups: { name?: string; entries: NavEntry[]; empty?: string }[];
  children: ReactNode;
}) {
  return (
    <div className="section">
      <nav className="section-nav" aria-labelledby="section-title">
        <h1 id="section-title">{title}</h1>
        {groups.map((group, position) => (
          <div className="nav-group" key={group.name ?? position}>
            {group.name && <span>{group.name}</span>}
            {group.entries.length === 0 && group.empty && <p className="nav-empty">{group.empty}</p>}
            {group.entries.map((entry) => (
              <a
                key={entry.key}
                className="nav-item"
                href={entry.href}
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
        {note && <span className="note">{note}</span>}
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="page-empty">{children}</p>;
}
