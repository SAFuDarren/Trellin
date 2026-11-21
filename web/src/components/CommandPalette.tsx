import { useLayoutEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { paletteQuery } from "../shell/paletteQuery";
import { formatShortcut, SHORTCUTS } from "../shortcuts";
import { PickerContent, type FloatingGroup } from "./FloatingControls";
import "../styles/command-palette.css";

export function CommandPalette({
  open,
  groups,
  onClose,
  onQueryChange,
  loading = false,
}: {
  open: boolean;
  groups: FloatingGroup[];
  onClose: () => void;
  onQueryChange: (query: string) => void;
  loading?: boolean;
}) {
  const { t } = useTranslation();
  const dialog = useRef<HTMLDialogElement>(null);
  const previousFocus = useRef<HTMLElement | null>(null);

  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) {
      previousFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      element.showModal();
      element.querySelector<HTMLInputElement>("[data-floating-focus]")?.focus({ preventScroll: true });
    } else if (!open && element.open) {
      element.close();
      previousFocus.current?.focus({ preventScroll: true });
    }
  }, [open]);

  function dismiss() {
    dialog.current?.close();
    previousFocus.current?.focus({ preventScroll: true });
    onClose();
  }

  return (
    <dialog
      ref={dialog}
      className="command-palette"
      aria-label={t("palette.title")}
      onCancel={(event) => {
        event.preventDefault();
        dismiss();
      }}
      onKeyDown={(event) => {
        if (event.key !== "Escape" || event.nativeEvent.isComposing || event.keyCode === 229) return;
        event.preventDefault();
        event.stopPropagation();
        dismiss();
      }}
      onClick={(event) => {
        if (event.target === event.currentTarget) dismiss();
      }}
    >
      {open && (
        <PickerContent
          groups={groups}
          searchLabel={t("palette.search")}
          emptyLabel={t(loading ? "palette.loading" : "palette.noResults")}
          limit={{ perGroup: 8, label: t("palette.refine") }}
          filterText={(query) => paletteQuery(query).text}
          onQueryChange={onQueryChange}
          onChoose={(option) => {
            dismiss();
            if (option.onSelect) option.onSelect();
            else if (option.href) window.location.assign(option.href);
          }}
          searchAction={
            <button
              type="button"
              className="command-palette-close"
              aria-label={t("palette.close")}
              aria-keyshortcuts={SHORTCUTS.escape}
              onClick={dismiss}
            >
              <kbd>{formatShortcut(SHORTCUTS.escape)}</kbd>
            </button>
          }
          footer={
            <div className="command-palette-keys" role="group" aria-label={t("palette.keyboardHelp")}>
              <span>
                <kbd>{formatShortcut("ArrowUp")}</kbd>
                <kbd>{formatShortcut("ArrowDown")}</kbd>
                {t("palette.navigate")}
              </span>
              <span>
                <kbd>{formatShortcut(SHORTCUTS.enter)}</kbd>
                {t("palette.open")}
              </span>
              <span>
                <kbd>#</kbd>
                {t("palette.claimSearch")}
              </span>
              <span>
                <kbd>@</kbd>
                {t("palette.agents")}
              </span>
            </div>
          }
        />
      )}
    </dialog>
  );
}
