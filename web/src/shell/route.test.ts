import { describe, expect, it } from "vitest";
import { parseRoute, parseWorkbenchAddress, workbenchHref, workbenchSub } from "./route";

const CLAIM = "11111111-1111-4111-8111-111111111111";
const NODE = "obligation:55555555-5555-4555-8555-555555555555:2";

describe("workbench addresses (#274)", () => {
  it("round-trips a claim and a node through a readable hash", () => {
    const hash = workbenchHref({ claimId: CLAIM, nodeId: NODE });
    expect(hash).toBe(`#workbench/claim/${CLAIM}/node/${encodeURIComponent(NODE)}`);
    const route = parseRoute(hash);
    expect(route.section).toBe("workbench");
    expect(parseWorkbenchAddress(route.sub)).toEqual({ claimId: CLAIM, nodeId: NODE });
  });

  it("names a claim without a node, and the map without either", () => {
    expect(parseWorkbenchAddress(parseRoute(workbenchHref({ claimId: CLAIM })).sub)).toEqual({ claimId: CLAIM });
    expect(workbenchHref(null)).toBe("#workbench");
    expect(parseWorkbenchAddress(parseRoute("#workbench").sub)).toBeNull();
  });

  it("ignores addresses that name no claim", () => {
    for (const sub of ["", "claim", "claim/", "node/x", "something/else"]) {
      expect(parseWorkbenchAddress(sub), sub).toBeNull();
    }
    expect(parseWorkbenchAddress(`claim/${CLAIM}/node`)).toEqual({ claimId: CLAIM });
    expect(workbenchSub({ claimId: CLAIM, nodeId: NODE })).toBe(`claim/${CLAIM}/node/${NODE}`);
  });
});
