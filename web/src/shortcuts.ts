const arrows: Record<string, string> = {
  ArrowUp: "↑",
  ArrowDown: "↓",
  ArrowLeft: "←",
  ArrowRight: "→",
};

const macKeys: Record<string, string> = {
  Primary: "⌘",
  Meta: "⌘",
  Control: "⌃",
  Alt: "⌥",
  Shift: "⇧",
  Enter: "↵",
  Backspace: "⌫",
  Delete: "⌦",
  Tab: "⇥",
};

const keyNames: Record<string, string> = {
  Primary: "Ctrl",
  Control: "Ctrl",
  Escape: "Esc",
  Plus: "+",
  Minus: "−",
};

const paletteKey = "K";
const contextWithShift = "F10";

export const SHORTCUTS = {
  paletteKey,
  palette: `Primary+${paletteKey}`,
  sheet: "?",
  contextMenu: `Shift+${contextWithShift}`,
  contextWithShift,
  contextMenuKey: "ContextMenu",
  details: "I",
  atlas: "M",
  linkClaim: "L",
  arrows: ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"],
  zoom: ["Plus", "Minus"],
  fit: "0",
  enter: "Enter",
  escape: "Escape",
} as const;

export function isMacPlatform(platform = globalThis.navigator?.platform ?? "") {
  return /^Mac/i.test(platform);
}

/** Format a shortcut for display. Keyboard handlers and aria-keyshortcuts use the original key names. */
export function formatShortcut(shortcut: string, mac = isMacPlatform()): string {
  return shortcut
    .split("+")
    .map((key) => (mac ? macKeys[key] : undefined) ?? arrows[key] ?? keyNames[key] ?? key)
    .join(mac ? "" : "+");
}
