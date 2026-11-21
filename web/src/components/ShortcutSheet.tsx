import { useId, useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { formatShortcut, SHORTCUTS } from "../shortcuts";
import { graphActionLabel, graphActionShortcut } from "../workbench/actions";
import "../styles/shortcut-sheet.css";

const ACTIONS = ["add-theorem", "open-proof", "proof-task", "ask-agent", "copy-reference", "copy-lean"] as const;
type Row = { id: string; label: string; keys: readonly string[] };

function ShortcutGroup({ title, rows }: { title: string; rows: readonly Row[] }) {
  const heading = useId();
  return (
    <section aria-labelledby={heading} className="shortcut-sheet-group">
      <h3 id={heading}>{title}</h3>
      <dl>
        {rows.map((row) => (
          <div key={row.id}>
            <dt>{row.label}</dt>
            <dd>
              {row.keys.map((key) => (
                <kbd key={key}>{formatShortcut(key)}</kbd>
              ))}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

export function ShortcutSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const title = useId();
  const dialog = useRef<HTMLDialogElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  useLayoutEffect(() => {
    const target = dialog.current;
    if (!target) return;
    if (open && !target.open) {
      previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      target.showModal();
      close.current?.focus({ preventScroll: true });
    } else if (!open && target.open) {
      target.close();
      previousFocus.current?.focus({ preventScroll: true });
    }
  }, [open]);

  return (
    <dialog
      ref={dialog}
      className="shortcut-sheet"
      aria-labelledby={title}
      onCancel={(event) => {
        event.preventDefault();
        onClose();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <header>
        <h2 id={title}>{t("shortcuts.title")}</h2>
        <button
          ref={close}
          type="button"
          className="icon-button"
          aria-keyshortcuts={SHORTCUTS.escape}
          aria-label={t("shortcuts.close")}
          onClick={onClose}
        >
          ×
        </button>
      </header>
      <div className="shortcut-sheet-body" role="region" tabIndex={0} aria-label={t("shortcuts.list")}>
        <ShortcutGroup
          title={t("shortcuts.navigation")}
          rows={[
            { id: "palette", label: t("palette.title"), keys: [SHORTCUTS.palette] },
            { id: "tab", label: t("shortcuts.focus"), keys: ["Tab", "Shift+Tab"] },
            { id: "context", label: t("shortcuts.contextMenu"), keys: [SHORTCUTS.contextMenu] },
            { id: "close", label: t("shortcuts.dismiss"), keys: [SHORTCUTS.escape] },
          ]}
        />
        <ShortcutGroup
          title={t("shortcuts.canvas")}
          rows={[
            { id: "pan", label: t("shortcuts.pan"), keys: SHORTCUTS.arrows },
            { id: "zoom", label: t("stage.zoom"), keys: SHORTCUTS.zoom },
            { id: "fit", label: t("stage.fit"), keys: [SHORTCUTS.fit] },
            { id: "details", label: t("canvasShell.details"), keys: [SHORTCUTS.details] },
            { id: "atlas", label: t("sidebar.atlas"), keys: [SHORTCUTS.atlas] },
            { id: "proof", label: t("shortcuts.openSelected"), keys: [SHORTCUTS.enter] },
            { id: "move", label: t("shortcuts.previewMove"), keys: SHORTCUTS.arrows },
            { id: "save", label: t("shortcuts.saveMove"), keys: [SHORTCUTS.enter] },
            { id: "cancel", label: t("shortcuts.cancelMove"), keys: [SHORTCUTS.escape] },
          ]}
        />
        <ShortcutGroup
          title={t("shortcuts.menuActions")}
          rows={ACTIONS.flatMap((id) => {
            const shortcut = graphActionShortcut(id);
            return shortcut ? [{ id, label: graphActionLabel(t, id), keys: [shortcut] }] : [];
          })}
        />
      </div>
    </dialog>
  );
}
