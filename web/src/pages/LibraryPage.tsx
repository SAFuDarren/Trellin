import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { useLibrary, type LibraryItem } from "../api/sections";
import { Empty, PageHead, SectionLayout } from "../components/SectionLayout";
import { href } from "../shell/route";
import { summarize, timeText } from "../workbench/text";

export function itemName(t: TFunction, item: LibraryItem): string {
  if (item.declaration) return item.declaration;
  if (item.natural) return summarize(item.natural.split("\n")[0] ?? "", 48);
  return t(`library.kinds.${item.kind ?? "item"}`, { defaultValue: item.kind ?? item.logical_key });
}

export function LibraryPage({
  session,
  sub,
  onOpen,
}: {
  session: string;
  sub: string;
  onOpen: (claimId: string) => void;
}) {
  const { t, i18n } = useTranslation();
  const library = useLibrary(session);
  const [query, setQuery] = useState("");
  const items = library.data?.items ?? [];
  const [filterKey = "", selectedId = ""] = sub.split("~");
  const kinds = [...new Set(items.map((item) => item.kind ?? "item"))].sort();
  const origins = [...new Set(items.map((item) => item.origin))].sort();
  const matchesFilter = (item: LibraryItem) => {
    if (!filterKey) return true;
    const [type, value] = filterKey.split(":");
    if (type === "kind") return (item.kind ?? "item") === value;
    if (type === "origin") return item.origin === value;
    if (type === "state") return value === "recheck" ? item.needs_recheck : item.retired;
    return true;
  };
  const needle = query.trim().toLowerCase();
  const shown = items.filter(
    (item) =>
      matchesFilter(item) &&
      (!needle ||
        [item.declaration, item.natural, item.lean, item.formula, item.logical_key].some((value) =>
          value?.toLowerCase().includes(needle),
        )),
  );
  const selected = shown.find((item) => item.id === selectedId) ?? shown[0];
  const link = (filter: string) => href("library", filter);
  const entry = (filter: string, label: string, count: number) => ({
    key: filter,
    label,
    href: link(filter),
    current: filterKey === filter,
    count,
  });
  return (
    <SectionLayout
      title={t("rail.library")}
      groups={[
        { entries: [entry("", t("library.all"), items.length)] },
        {
          name: t("library.byKind"),
          entries: kinds.map((kind) =>
            entry(
              "kind:" + kind,
              t(`library.kinds.${kind}`, { defaultValue: kind }),
              items.filter((item) => (item.kind ?? "item") === kind).length,
            ),
          ),
        },
        {
          name: t("library.bySource"),
          entries: origins.map((origin) =>
            entry(
              "origin:" + origin,
              t(`library.origins.${origin}`, { defaultValue: origin }),
              items.filter((item) => item.origin === origin).length,
            ),
          ),
        },
        {
          name: t("library.attention"),
          entries: [
            entry("state:recheck", t("library.needsRecheck"), items.filter((item) => item.needs_recheck).length),
            entry("state:retired", t("library.withdrawn"), items.filter((item) => item.retired).length),
          ],
        },
      ]}
    >
      <PageHead title={t("rail.library")} note={t("library.count", { count: shown.length })} />
      <div className="page-body split">
        <div className="primary">
          <label className="search-field">
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <circle cx="11" cy="11" r="6.5" />
              <path d="m20 20-4.2-4.2" />
            </svg>
            <input
              type="search"
              placeholder={t("library.search")}
              aria-label={t("library.search")}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
          {library.isError ? (
            <Empty>{library.error.message}</Empty>
          ) : shown.length === 0 ? (
            <Empty>{library.isPending ? t("common.loading") : t("library.none")}</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">{t("library.name")}</th>
                    <th scope="col">{t("library.statement")}</th>
                    <th scope="col">{t("library.version")}</th>
                    <th scope="col" className="num">
                      {t("library.usedBy")}
                    </th>
                    <th scope="col">{t("library.source")}</th>
                  </tr>
                </thead>
                <tbody>
                  {shown.map((item) => (
                    <tr
                      key={item.id}
                      data-row="true"
                      aria-selected={item === selected}
                      tabIndex={0}
                      onClick={() => (window.location.hash = link((filterKey || "") + "~" + item.id))}
                      onKeyDown={(event) => {
                        if (event.key === "Enter") window.location.hash = link((filterKey || "") + "~" + item.id);
                      }}
                    >
                      <td className="mono">{itemName(t, item)}</td>
                      <td className="math">{summarize(item.lean ?? item.formula ?? "", 60)}</td>
                      <td className="muted">{"v" + item.version}</td>
                      <td className="num">{item.used_by.length}</td>
                      <td className="muted">{t(`library.origins.${item.origin}`, { defaultValue: item.origin })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
        {selected && (
          <aside className="aside" aria-label={itemName(t, selected)}>
            <span className="field-label">
              {[
                t(`library.kinds.${selected.kind ?? "item"}`, { defaultValue: selected.kind ?? "" }),
                t(`library.origins.${selected.origin}`, { defaultValue: selected.origin }),
              ].join(" · ")}
            </span>
            <div className="title-row">
              <h3 className="mono">{itemName(t, selected)}</h3>
            </div>
            {selected.natural && <p>{selected.natural}</p>}
            {selected.formula && <p className="formula">{selected.formula}</p>}
            {selected.lean && <pre className="code-block">{selected.lean}</pre>}
            <dl className="facts">
              <dt>{t("library.version")}</dt>
              <dd>{"v" + selected.version + " · " + timeText(i18n.language, selected.created_at, true)}</dd>
              <dt>{t("library.state")}</dt>
              <dd>
                {selected.retired
                  ? t("library.withdrawn")
                  : selected.needs_recheck
                    ? t("library.needsRecheck")
                    : selected.admitted
                      ? t("library.admitted")
                      : t("library.notAdmitted")}
              </dd>
              <dt>{t("library.usedBy")}</dt>
              <dd>
                {selected.used_by.length === 0
                  ? t("library.unused")
                  : selected.used_by.map((use) => (
                      <button type="button" className="relation" key={use.id} onClick={() => onOpen(use.id)}>
                        {use.title || t("sidebar.untitled")}
                      </button>
                    ))}
              </dd>
            </dl>
          </aside>
        )}
      </div>
    </SectionLayout>
  );
}
