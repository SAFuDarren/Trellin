import type { TFunction } from "i18next";

const LABELS = {
  "open-proof": "projectCanvas.openProof",
  "proof-task": "proofTask.title",
  "ask-agent": "projectCanvas.askAgent",
  "open-details": "graphActions.openDetails",
  "write-note": "graphActions.writeNote",
  "copy-reference": "graphActions.copyReference",
  "copy-lean": "graphActions.copyLean",
  "show-library": "graphActions.showLibrary",
} as const;

type ActionId = keyof typeof LABELS;

export function graphAction(t: TFunction, id: ActionId, onSelect: (() => void) | undefined, detail?: string) {
  return { id, label: t(LABELS[id]), disabled: !onSelect, detail, onSelect };
}

export function graphCopyActions(
  t: TFunction,
  values: { reference?: string; lean?: string; unavailable?: string },
  copy: (text: string) => void,
) {
  const { reference, lean, unavailable } = values;
  return [
    graphAction(
      t,
      "copy-reference",
      reference ? () => copy(reference) : undefined,
      reference ? undefined : (unavailable ?? t("graphActions.noReference")),
    ),
    graphAction(
      t,
      "copy-lean",
      lean?.trim() ? () => copy(lean) : undefined,
      lean?.trim() ? undefined : (unavailable ?? t("graphActions.noLean")),
    ),
  ];
}
