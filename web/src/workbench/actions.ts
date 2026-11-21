import { SHORTCUTS } from "../shortcuts";
import type { TFunction } from "i18next";
import { createElement } from "react";
import { WorkbenchIcon } from "../components/Icons";

type ActionDefinition = { label: string; icon: string; shortcut?: string; search?: string };

const ACTIONS = {
  "add-theorem": { label: "projectEntry.addTheorem", icon: "add", shortcut: "Alt+N" },
  "organize-research": { label: "projectEntry.organize", icon: "organize" },
  "research-references": {
    label: "claimCodes.title",
    icon: "reference",
    shortcut: "Alt+R",
    search: "palette.referenceKeywords",
  },
  "open-proof": { label: "projectCanvas.openProof", icon: "proof", shortcut: "Alt+P" },
  "proof-task": { label: "proofTask.title", icon: "task", shortcut: "Alt+T" },
  "ask-agent": { label: "projectCanvas.askAgent", icon: "chat", shortcut: "Alt+A" },
  "start-chat": { label: "palette.startChat", icon: "chat", shortcut: "Alt+A" },
  "open-details": { label: "graphActions.openDetails", icon: "details", shortcut: "Alt+D" },
  "write-note": { label: "graphActions.writeNote", icon: "note" },
  "copy-reference": { label: "graphActions.copyReference", icon: "copy", shortcut: "Alt+C" },
  "copy-lean": { label: "graphActions.copyLean", icon: "code", shortcut: "Alt+L" },
  "show-library": { label: "graphActions.showLibrary", icon: "library" },
  "link-claim": { label: "projectCanvas.linkClaim", icon: "link", shortcut: SHORTCUTS.linkClaim },
  "cancel-link": { label: "projectCanvas.cancelLink", icon: "cancel" },
  "move-card": { label: "projectCanvas.moveCard", icon: "move" },
} satisfies Record<string, ActionDefinition>;

type ActionId = keyof typeof ACTIONS;

export type ProjectAction = "add-theorem" | "organize-research" | "research-references" | "proof-task";
export type ProjectActionRequest = { id: Exclude<ProjectAction, "proof-task"> } | { id: "proof-task"; claimId: string };

export function graphActionLabel(t: TFunction, id: ActionId) {
  return t(ACTIONS[id].label);
}

export function graphActionShortcut(id: ActionId): string | undefined {
  const action: ActionDefinition = ACTIONS[id];
  return action.shortcut;
}

export function graphAction(t: TFunction, id: ActionId, onSelect: (() => void) | undefined, detail?: string) {
  const action: ActionDefinition = ACTIONS[id];
  return {
    id,
    label: graphActionLabel(t, id),
    icon: createElement(WorkbenchIcon, { name: action.icon }),
    shortcut: onSelect ? action.shortcut : undefined,
    searchText: action.search && t(action.search),
    disabled: !onSelect,
    detail,
    onSelect,
  };
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
