import { describe, expect, it } from "vitest";
import { atlasOpening } from "./AtlasMap";

const card = (x: number, y: number) => ({ x, y, w: 192, h: 80 });

describe("atlasOpening (#154)", () => {
  it("opens in the top left when no card is there", () => {
    expect(atlasOpening(900, 600, [card(400, 300)])).toMatchObject({ x: 18, y: 14 });
  });

  it("moves to the top right when cards occupy the top left", () => {
    expect(atlasOpening(900, 600, [card(60, 40), card(60, 160)])).toMatchObject({ x: 900 - 186 - 18, y: 14 });
  });

  it("uses the bottom left when both top corners hold cards", () => {
    const box = atlasOpening(900, 600, [card(20, 20), card(700, 20)]);
    expect(box.x).toBe(18);
    expect(box.y).toBeGreaterThan(300);
  });

  it("keeps the first corner when every corner overlaps equally", () => {
    expect(atlasOpening(900, 600, [])).toMatchObject({ x: 18, y: 14, w: 186 });
  });

  it("opens compact on narrow maps and avoids the selected claim", () => {
    const box = atlasOpening(528, 321, [
      { ...card(20, 75), selected: true },
      card(369, 75),
      card(20, 168),
      card(194, 168),
    ]);
    expect(box.w).toBe(124);
    // The compact overview draws about 3/4 of its width plus 30px of caption and padding.
    const drawn = (box.w * 3) / 4 + 30;
    const coversSelected = box.x < 20 + 192 && 20 < box.x + box.w && box.y < 75 + 80 && 75 < box.y + drawn;
    expect(coversSelected).toBe(false);
  });
});
