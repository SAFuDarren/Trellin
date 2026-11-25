// #289: the rail shows a few spaces, and each space's panel holds its second level, as in Multica.
// Every page belongs to exactly one space. A new page is added here, not to the rail.
import { href, parseRoute, type Route, type Section } from "./route";

export type SpaceId = "home" | "work" | "chat" | "team" | "automation";

export type SpaceEntry = {
  key: string;
  section: Section;
  /** The sub-route this entry opens. Other sub-routes of the same section belong to `owns`. */
  sub?: string;
  labelKey: string;
};

export type Space = { id: SpaceId; labelKey: string; entries: SpaceEntry[] };

export const SPACES: Space[] = [
  {
    id: "home",
    labelKey: "spaces.home",
    entries: [{ key: "map", section: "workbench", labelKey: "spaces.entries.map" }],
  },
  {
    id: "work",
    labelKey: "spaces.work",
    entries: [
      // #326: Work opens on the claims board.
      { key: "claims", section: "claims", labelKey: "spaces.entries.claims" },
      { key: "inbox", section: "agent-work", sub: "inbox", labelKey: "spaces.entries.inbox" },
      { key: "review", section: "review", labelKey: "spaces.entries.review" },
      { key: "agent-tasks", section: "agent-work", labelKey: "spaces.entries.agentTasks" },
      { key: "library", section: "library", labelKey: "spaces.entries.library" },
    ],
  },
  { id: "chat", labelKey: "spaces.chat", entries: [{ key: "chat", section: "chat", labelKey: "spaces.chat" }] },
  { id: "team", labelKey: "spaces.team", entries: [{ key: "team", section: "team", labelKey: "spaces.team" }] },
  {
    id: "automation",
    labelKey: "spaces.automation",
    entries: [{ key: "automation", section: "progress", labelKey: "spaces.automation" }],
  },
];

/** Sections reached from the rail foot rather than a space. */
export const UTILITY_SECTIONS: Section[] = ["settings", "help"];

export function entryHref(entry: SpaceEntry): string {
  return href(entry.section, entry.sub);
}

/** The entry a route shows. A sub-route opens its entry when one names it, else the section's plain entry. */
export function currentEntry(route: Route): SpaceEntry | undefined {
  const entries = SPACES.flatMap((space) => space.entries).filter((entry) => entry.section === route.section);
  const head = route.sub.split("/")[0];
  return entries.find((entry) => entry.sub !== undefined && entry.sub === head) ?? entries.find((entry) => !entry.sub);
}

export function spaceOf(section: Section): Space | undefined {
  return SPACES.find((space) => space.entries.some((entry) => entry.section === section));
}

export function currentRoute(): Route {
  return parseRoute(window.location.hash);
}
