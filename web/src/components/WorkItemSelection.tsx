import { useState, type KeyboardEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

export function useWorkItemSelection(scope: string, ids: readonly string[]) {
  const [state, setState] = useState<{ scope: string; selected: ReadonlySet<string>; anchor?: string }>(() => ({
    scope,
    selected: new Set(),
  }));
  if (state.scope !== scope) setState({ scope, selected: new Set() });

  const selected = ids.filter((id) => state.scope === scope && state.selected.has(id));
  const selectedIds = new Set(selected);
  function toggle(id: string, range: boolean) {
    const start = state.anchor ? ids.indexOf(state.anchor) : -1;
    const end = ids.indexOf(id);
    if (end < 0) return;
    if (range && start >= 0) {
      for (const member of ids.slice(Math.min(start, end), Math.max(start, end) + 1)) selectedIds.add(member);
      setState({ scope, selected: selectedIds, anchor: state.anchor });
    } else {
      if (!selectedIds.delete(id)) selectedIds.add(id);
      setState({ scope, selected: selectedIds, anchor: id });
    }
  }

  return {
    selected,
    contains: (id: string) => state.scope === scope && state.selected.has(id),
    toggle,
    clear: () => setState({ scope, selected: new Set() }),
    selectAll: () => setState({ scope, selected: new Set(ids), anchor: state.anchor ?? ids[0] }),
  };
}

type Selection = ReturnType<typeof useWorkItemSelection>;

export function WorkSelectionCheckbox({ id, title, selection }: { id: string; title: string; selection: Selection }) {
  const { t } = useTranslation();
  return (
    <input
      className="work-selection-checkbox"
      type="checkbox"
      checked={selection.contains(id)}
      aria-label={t("workSelection.select", { title })}
      onChange={(event) => selection.toggle(id, event.nativeEvent instanceof MouseEvent && event.nativeEvent.shiftKey)}
    />
  );
}

export function WorkSelectionArea({
  label,
  selection,
  children,
}: {
  label: string;
  selection: Selection;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [copyResult, setCopyResult] = useState<{ references: string; status: "copied" | "copyFailed" }>();
  const references = selection.selected.join("\n");
  const count = selection.selected.length;
  function clear() {
    selection.clear();
    setCopyResult(undefined);
  }
  function shortcut(event: KeyboardEvent<HTMLDivElement>) {
    if (event.defaultPrevented || event.nativeEvent.isComposing || event.altKey) return;
    const target = event.target;
    if (
      !(target instanceof Element) ||
      target.closest("textarea, select, input:not([type='checkbox']), [contenteditable]")
    )
      return;
    if (event.key === "Escape" && count) {
      event.preventDefault();
      if (target.closest(".work-selection-actions")) event.currentTarget.focus();
      clear();
    } else if (event.key.toLowerCase() === "a" && (event.ctrlKey || event.metaKey) && !event.shiftKey) {
      event.preventDefault();
      setCopyResult(undefined);
      selection.selectAll();
    }
  }
  async function copy() {
    setCopyResult(undefined);
    try {
      await navigator.clipboard.writeText(references);
      setCopyResult({ references, status: "copied" });
    } catch {
      setCopyResult({ references, status: "copyFailed" });
    }
  }
  return (
    <div
      className="work-selection-area"
      role="region"
      aria-label={t("workSelection.region", { label })}
      tabIndex={0}
      onKeyDown={shortcut}
    >
      {children}
      {/* After the list and floating at its bottom edge, so selecting never moves a row (#272, #283). */}
      {count > 0 && (
        <div className="work-selection-actions" role="group" aria-label={t("workSelection.actions")}>
          <span role="status">{t("workSelection.count", { count })}</span>
          <button type="button" className="button" onClick={() => void copy()}>
            {t("workSelection.copy")}
          </button>
          <button
            type="button"
            className="button"
            onClick={(event) => {
              event.currentTarget.closest<HTMLElement>(".work-selection-area")?.focus();
              clear();
            }}
          >
            {t("workSelection.clear")}
          </button>
          <span className="work-selection-notice" role="status">
            {copyResult?.references === references && t(`workSelection.${copyResult.status}`)}
          </span>
        </div>
      )}
    </div>
  );
}
