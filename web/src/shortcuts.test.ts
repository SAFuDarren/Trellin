import { describe, expect, it } from "vitest";
import { formatShortcut, isMacPlatform } from "./shortcuts";

describe("platform shortcut display", () => {
  it.each(["MacIntel", "MacPPC"])("detects macOS from %s", (platform) => {
    expect(isMacPlatform(platform)).toBe(true);
  });
  it.each(["Win32", "Linux x86_64", "", "iPhone"])("keeps key names on %s", (platform) => {
    expect(isMacPlatform(platform)).toBe(false);
  });
  it.each([
    ["Alt+N", "⌥N", "Alt+N"],
    ["Primary+K", "⌘K", "Ctrl+K"],
    ["Shift+Primary+Enter", "⇧⌘↵", "Shift+Ctrl+Enter"],
    ["Control+ArrowDown", "⌃↓", "Ctrl+↓"],
    ["Shift+F10", "⇧F10", "Shift+F10"],
    ["Escape", "Esc", "Esc"],
    ["I", "I", "I"],
  ])("formats %s as %s or %s", (input, mac, other) => {
    expect(formatShortcut(input, true)).toBe(mac);
    expect(formatShortcut(input, false)).toBe(other);
  });
});
