import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { ReleaseUpdates } from "../api/releaseUpdates";
import { LANGUAGES, setLanguage, type Language } from "../i18n";
import { ReleaseNotesLink } from "../components/ReleaseUpdateNotice";
import { SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import "../styles/settings.css";

type Setting = {
  id: string;
  label: string;
  note?: string;
  terms?: string[];
} & ({ control: ReactNode } | { action: string; destination: string });

function settingHref(entry: Setting): string {
  return "destination" in entry ? entry.destination : href("settings", entry.id);
}

function focusSetting(id: string): void {
  const heading = document.getElementById(`setting-${id}`);
  heading?.focus({ preventScroll: true });
  heading?.scrollIntoView({ block: "start" });
}

const normalize = (value: string) => value.normalize("NFKC").toLocaleLowerCase();

export function SettingsPage({
  sub,
  onResetLayout,
  onShowShortcuts,
  updates,
}: {
  sub: string;
  onResetLayout: () => void;
  onShowShortcuts: () => void;
  updates: ReleaseUpdates;
}) {
  const { t, i18n } = useTranslation();
  const [query, setQuery] = useState("");
  const hasUpdates = Boolean(updates.state);
  useEffect(() => setQuery(""), [sub]);
  useEffect(() => {
    if (!sub) return;
    const frame = requestAnimationFrame(() => focusSetting(sub));
    return () => cancelAnimationFrame(frame);
  }, [sub, hasUpdates]);

  const groups: { id: string; label: string; entries: Setting[] }[] = [
    {
      id: "personal",
      label: t("settings.groups.personal"),
      entries: [
        {
          id: "language",
          label: t("settings.language"),
          note: t("settings.languageNote"),
          terms: Object.values(LANGUAGES),
          control: (
            <div className="choices" role="group" aria-labelledby="setting-language">
              {(Object.entries(LANGUAGES) as [Language, string][]).map(([code, name]) => (
                <button
                  key={code}
                  type="button"
                  lang={code}
                  className="button"
                  aria-pressed={i18n.language === code}
                  onClick={() => setLanguage(code)}
                >
                  {name}
                </button>
              ))}
            </div>
          ),
        },
        {
          id: "layout",
          label: t("settings.layout"),
          note: t("settings.layoutNote"),
          terms: [t("settings.resetLayout")],
          control: (
            <button type="button" className="button" onClick={onResetLayout}>
              {t("settings.resetLayout")}
            </button>
          ),
        },
        {
          id: "shortcuts",
          label: t("shortcuts.title"),
          note: t("settings.shortcutsNote"),
          control: (
            <button type="button" className="button" aria-haspopup="dialog" onClick={onShowShortcuts}>
              {t("settings.showShortcuts")}
            </button>
          ),
        },
      ],
    },
    {
      id: "research",
      label: t("settings.groups.research"),
      entries: [
        {
          id: "automation",
          label: t("rail.progress"),
          note: t("settings.automationNote"),
          action: t("settings.openAutomation"),
          destination: href("progress"),
        },
      ],
    },
    {
      id: "connections",
      label: t("settings.groups.connections"),
      entries: [
        {
          id: "models",
          label: t("modelConnections.title"),
          note: t("team.notes.models"),
          action: t("settings.openModels"),
          destination: href("team", "models"),
        },
        {
          id: "runtimes",
          label: t("settings.environment"),
          note: t("settings.environmentNote"),
          action: t("settings.openRuntimes"),
          destination: href("team", "runtimes"),
        },
      ],
    },
    {
      id: "about",
      label: t("settings.groups.about"),
      entries: [
        ...(hasUpdates
          ? [
              {
                id: "updates",
                label: t("settings.updates.title"),
                terms: [
                  t("settings.updates.installed", { version: updates.state?.installed_version }),
                  t("settings.updates.check"),
                  t("settings.updates.automatic"),
                  t("settings.updates.notes"),
                ],
                control: <UpdateSettings updates={updates} />,
              },
            ]
          : []),
        {
          id: "help",
          label: t("rail.help"),
          note: t("settings.helpNote"),
          action: t("settings.openHelp"),
          destination: href("help"),
        },
      ],
    },
  ];
  const words = normalize(query).trim().split(/\s+/).filter(Boolean);
  const results = groups.flatMap(({ label, entries }) =>
    entries.flatMap((entry) => {
      const action = "action" in entry ? entry.action : "";
      const haystack = normalize([label, entry.label, entry.note, action, ...(entry.terms ?? [])].join(" "));
      return words.every((word) => haystack.includes(word)) ? [{ group: label, entry }] : [];
    }),
  );

  return (
    <SectionLayout
      title={t("rail.settings")}
      groups={groups.map(({ label, entries }) => ({
        name: label,
        entries: entries.map((entry) => ({
          key: entry.id,
          label: entry.label,
          href: settingHref(entry),
          current: sub === entry.id,
        })),
      }))}
    >
      <div className="page-head settings-toolbar" role="search">
        <label className="search-field">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="10.5" cy="10.5" r="6" />
            <path d="m15 15 5 5" />
          </svg>
          <input
            type="search"
            aria-label={t("settings.search")}
            placeholder={t("settings.search")}
            value={query}
            onChange={(event) => setQuery(event.currentTarget.value)}
            onKeyDown={(event) => {
              if (event.key !== "Escape" || !query) return;
              event.preventDefault();
              event.stopPropagation();
              setQuery("");
            }}
          />
        </label>
      </div>
      {words.length > 0 ? (
        <div className="page-body settings">
          <p className={results.length ? "sr-only" : "page-note"} role="status">
            {results.length ? t("settings.matches", { count: results.length }) : t("settings.noMatches")}
          </p>
          {results.length > 0 && (
            <ul className="settings-results" aria-label={t("settings.results")}>
              {results.map(({ group, entry }) => (
                <li key={entry.id}>
                  <a
                    href={settingHref(entry)}
                    onClick={() => {
                      setQuery("");
                      if (entry.id === sub) requestAnimationFrame(() => focusSetting(entry.id));
                    }}
                  >
                    <strong>{entry.label}</strong>
                    <span>{group}</span>
                  </a>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : (
        <div className="page-body settings">
          {groups.map(({ id, label, entries }) => (
            <section key={id} className="settings-group" aria-labelledby={`settings-group-${id}`}>
              <h2 id={`settings-group-${id}`}>{label}</h2>
              {entries.map((entry) => (
                <section key={entry.id} className="setting-item" aria-labelledby={`setting-${entry.id}`}>
                  <h3 id={`setting-${entry.id}`} tabIndex={-1}>
                    {entry.label}
                  </h3>
                  {entry.note && <p>{entry.note}</p>}
                  {"control" in entry ? (
                    entry.control
                  ) : (
                    <a className="button" href={entry.destination}>
                      {entry.action}
                    </a>
                  )}
                </section>
              ))}
            </section>
          ))}
        </div>
      )}
    </SectionLayout>
  );
}

function UpdateSettings({ updates: { state, pending, error, submit } }: { updates: ReleaseUpdates }) {
  const { t, i18n } = useTranslation();
  if (!state) return null;
  const release = state.available;
  const checking = pending === "check" || pending === "auto_check";
  const status = error ? "requestFailed" : checking ? "checking" : `status.${state.status}`;
  return (
    <>
      <p>
        {state.installed_version
          ? t("settings.updates.installed", { version: state.installed_version })
          : t("settings.updates.installedUnknown")}
      </p>
      <div className="choices">
        <button
          type="button"
          className="button"
          disabled={Boolean(pending)}
          onClick={() => submit({ action: "check" })}
        >
          {t("settings.updates.check")}
        </button>
        {release && <ReleaseNotesLink url={release.url} />}
      </div>
      <label className="update-preference">
        <input
          type="checkbox"
          checked={state.automatic}
          disabled={Boolean(pending)}
          onChange={(event) => submit({ action: "automatic", enabled: event.currentTarget.checked })}
        />
        {t("settings.updates.automatic")}
      </label>
      <p>{t("settings.updates.automaticNote")}</p>
      <p role="status">{t(`settings.updates.${status}`, { version: release?.version })}</p>
      {state.last_checked_at !== null && (
        <p>
          {t("settings.updates.lastChecked", {
            time: new Intl.DateTimeFormat(i18n.language, { dateStyle: "medium", timeStyle: "short" }).format(
              state.last_checked_at * 1000,
            ),
          })}
        </p>
      )}
    </>
  );
}
