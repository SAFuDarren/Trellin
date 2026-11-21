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
};

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
