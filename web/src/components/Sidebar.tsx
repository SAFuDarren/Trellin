import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Claim } from "../api/client";
import type { AtlasClaim } from "../workbench/types";
import { statusText } from "../workbench/text";

export function Sidebar({
  claims,
  atlasClaims,
  claimId,
  open,
  atlasOpen,
  workspaceState,
  onSelect,
  onToggle,
  onToggleAtlas,
  onRefresh,
  refreshing,
}: {
  claims: Claim[];
  atlasClaims: AtlasClaim[];
  claimId: string | null;
  open: boolean;
  atlasOpen: boolean;
  workspaceState: string;
  onSelect: (id: string) => void;
  onToggle: () => void;
  onToggleAtlas: () => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const search = useRef<HTMLInputElement>(null);
  const focusSearch = useRef(false);
  useEffect(() => {
    if (!open || !focusSearch.current) return;
    focusSearch.current = false;
    search.current?.focus();
  }, [open]);

  const counts = new Map(atlasClaims.map((claim) => [claim.id, claim]));
  const needle = query.trim().toLowerCase();
  const shown = claims.filter(
    (claim) =>
      !needle ||
      (claim.title || "").toLowerCase().includes(needle) ||
      (claim.domain || "").toLowerCase().includes(needle),
  );
  const groups = new Map<string, Claim[]>();
  for (const claim of shown) {
    const domain = claim.domain || t("sidebar.uncategorized");
    (groups.get(domain) ?? groups.set(domain, []).get(domain)!).push(claim);
  }
  const sorted = [...groups].sort(([a], [b]) => a.localeCompare(b));
  const searchIcon = (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <circle cx="11" cy="11" r="6.5" />
      <path d="m20 20-4.2-4.2" />
    </svg>
  );

  return (
    <nav className="sidebar" id="sidebar" aria-label={t("sidebar.label")}>
      <div className="brand">
        <span className="brand-name">{t("sidebar.label")}</span>
        <button
          type="button"
          className="icon-button sidebar-refresh"
          aria-label={t("sidebar.refresh")}
          title={t("sidebar.refresh")}
          disabled={refreshing}
          onClick={onRefresh}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path d="M20 11a8 8 0 1 0-2.3 5.7" />
            <path d="M20 4v7h-7" />
          </svg>
        </button>
        <button
          type="button"
          className="icon-button"
          aria-expanded={open}
          aria-controls="sidebar"
          aria-label={open ? t("sidebar.collapse") : t("sidebar.expand")}
          onClick={onToggle}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
            <path d="M9.5 4.5v15" />
          </svg>
        </button>
      </div>
      <div className="menu">
        {open ? (
          <label className="menu-item search">
            {searchIcon}
            <input
              ref={search}
              type="search"
              aria-label={t("sidebar.search")}
              placeholder={t("sidebar.search")}
              autoComplete="off"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </label>
        ) : (
          <button
            type="button"
            className="menu-item"
            aria-label={t("sidebar.search")}
            title={t("sidebar.search")}
            onClick={() => {
              focusSearch.current = true;
              onToggle();
            }}
          >
            {searchIcon}
          </button>
        )}
        <button
          type="button"
          className="menu-item"
          aria-pressed={atlasOpen}
          aria-label={t("sidebar.atlas")}
          title={t("sidebar.atlas")}
          onClick={onToggleAtlas}
        >
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <circle cx="12" cy="12" r="8" />
            <path d="M4 12h16" />
            <path d="M12 4c2.4 2.2 3.6 4.9 3.6 8s-1.2 5.8-3.6 8c-2.4-2.2-3.6-4.9-3.6-8S9.6 6.2 12 4Z" />
          </svg>
          <span className="menu-label">{t("sidebar.atlas")}</span>
        </button>
      </div>
      <div className="claim-list">
        {sorted.length === 0 && (
          <p className="claim-empty">{claims.length ? t("sidebar.noMatch") : t("sidebar.noClaims")}</p>
        )}
        {sorted.map(([domain, list]) => (
          <div className="claim-group" key={domain}>
            <span>{domain}</span>
            {list.map((claim) => {
              const record = counts.get(claim.id);
              const badge = record?.awaiting_review
                ? t("sidebar.awaitingReview", { count: record.awaiting_review })
                : record?.blocked
                  ? t("sidebar.blocked", { count: record.blocked })
                  : statusText(t, claim.status);
              const title = claim.title || t("sidebar.untitled");
              const tone = record?.awaiting_review
                ? "warn"
                : record?.blocked || ["needs_recheck", "refuted"].includes(claim.status)
                  ? "bad"
                  : claim.status === "awaiting_interpretation_review"
                    ? "warn"
                    : claim.status === "proved"
                      ? "ok"
                      : "none";
              return (
                <button
                  type="button"
                  className="claim"
                  key={claim.id}
                  title={title}
                  aria-current={claim.id === claimId ? "page" : undefined}
                  onClick={() => claim.id !== claimId && onSelect(claim.id)}
                >
                  <span className="claim-title">{title}</span>
                  <span
                    className="claim-status"
                    data-attention={String(Boolean(record?.awaiting_review || record?.blocked))}
                  >
                    <span className="status-dot" data-tone={tone} aria-hidden="true" />
                    {badge}
                  </span>
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <footer className="sidebar-foot">
        <span title={workspaceState}>{workspaceState}</span>
      </footer>
    </nav>
  );
}
