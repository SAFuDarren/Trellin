import { describe, expect, it } from "vitest";
import en from "../i18n/en.json";
import zhCN from "../i18n/zh-CN.json";
import { parseRoute, SECTIONS } from "./route";
import { currentEntry, entryHref, SPACES, spaceOf, UTILITY_SECTIONS } from "./spaces";

describe("navigation spaces (#289)", () => {
  it("puts every page in exactly one space or the rail foot", () => {
    for (const section of SECTIONS) {
      const spaces = SPACES.filter((space) => space.entries.some((entry) => entry.section === section));
      expect(spaces.length + (UTILITY_SECTIONS.includes(section) ? 1 : 0), section).toBe(1);
    }
  });

  it("gives no two entries the same destination", () => {
    const destinations = SPACES.flatMap((space) => space.entries.map(entryHref));
    expect(new Set(destinations).size).toBe(destinations.length);
  });

  it("keeps the rail to the agreed spaces", () => {
    expect(SPACES.map((space) => space.id)).toEqual(["home", "work", "chat", "team", "automation"]);
  });

  it("keeps Home focused on the map and gives Claims a place in Work", () => {
    expect(spaceOf("workbench")?.entries.map((entry) => entry.key)).toEqual(["map"]);
    expect(spaceOf("claims")?.id).toBe("work");
    expect(spaceOf("claims")?.entries.map((entry) => entry.key)).toEqual([
      "inbox",
      "review",
      "claims",
      "agent-tasks",
      "library",
    ]);
  });

  it("finds the entry for every address, including sub-routes and old addresses", () => {
    const entry = (hash: string) => currentEntry(parseRoute(hash))?.key;
    expect(entry("#workbench")).toBe("map");
    expect(entry("#claims")).toBe("claims");
    expect(entry("#claims/board")).toBe("claims");
    expect(entry("#agent-work/inbox")).toBe("inbox");
    expect(entry("#agent-work")).toBe("agent-tasks");
    expect(entry("#agent-work/activity")).toBe("agent-tasks");
    expect(entry("#claims/agent-work/waiting")).toBe("agent-tasks");
    expect(entry("#review/some-item")).toBe("review");
    expect(entry("#library")).toBe("library");
    expect(spaceOf("progress")?.id).toBe("automation");
  });

  it("names Agent tasks one way in both catalogs (#294)", () => {
    expect(JSON.stringify(en)).not.toMatch(/Agent work/i);
    expect(JSON.stringify(zhCN)).not.toMatch(/Agent 派工|派工功能/);
    expect(en.agentWork.title).toBe(en.spaces.entries.agentTasks);
    expect(zhCN.agentWork.title).toBe(zhCN.spaces.entries.agentTasks);
  });

  it("gives Agent activity no panel entry: the drawer opens it and links to its full page (#294)", () => {
    const destinations = SPACES.flatMap((space) => space.entries.map(entryHref));
    expect(destinations).not.toContain("#agent-work/activity");
    expect(currentEntry(parseRoute("#agent-work/activity"))?.key).toBe("agent-tasks");
  });
});
