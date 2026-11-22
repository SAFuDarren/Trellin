import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { ReleaseUpdates } from "../api/releaseUpdates";
import "../i18n";
import { SettingsPage } from "./SettingsPage";

const browserEntries = ["language", "layout", "shortcuts"];

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it.each([
  { environment: "browser", desktop: false, entries: browserEntries },
  {
    environment: "Mac app",
    desktop: true,
    entries: [...browserEntries, "open-folder", "library-location", "updates"],
  },
])("gives every $environment Settings entry a control on the page", ({ desktop, entries }) => {
  vi.stubGlobal("webkit", desktop ? { messageHandlers: { trellinProjects: { postMessage: vi.fn() } } } : undefined);
  const updates: ReleaseUpdates = {
    state: desktop
      ? { installed_version: "1.0.0", automatic: true, last_checked_at: null, status: "not_checked", available: null }
      : undefined,
    pending: undefined,
    error: false,
    submit: vi.fn(),
  };
  render(<SettingsPage sub="" onResetLayout={vi.fn()} onShowShortcuts={vi.fn()} updates={updates} />);

  const navigation = screen.getByRole("navigation");
  const links = within(navigation).getAllByRole("link");
  expect(links.map((link) => link.getAttribute("href"))).toEqual(entries.map((id) => `#settings/${id}`));
  expect(
    [...document.querySelectorAll(".setting-item")].map((section) => section.getAttribute("aria-labelledby")),
  ).toEqual(entries.map((id) => `setting-${id}`));

  for (const link of links) {
    const section = screen.getByRole("region", { name: link.textContent?.trim() });
    const controls = [...within(section).queryAllByRole("button"), ...within(section).queryAllByRole("checkbox")];
    expect(controls.length).toBeGreaterThan(0);
    for (const destination of within(section).queryAllByRole("link")) {
      expect(destination.getAttribute("href")).toMatch(/^#settings(?:\/|$)/);
    }
  }
});
