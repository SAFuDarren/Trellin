// SPDX-License-Identifier: Apache-2.0
import { expect, test, type Locator } from "@playwright/test";
import { openProof } from "./project-map";

async function edgeOf(node: Locator) {
  return node.evaluate((element) => {
    const { borderTopColor, boxShadow } = getComputedStyle(element);
    return { borderTopColor, boxShadow };
  });
}

for (const colorScheme of ["light", "dark"] as const) {
  test(`running proof nodes keep their selection and focus distinct in ${colorScheme}`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.emulateMedia({ colorScheme, reducedMotion: "reduce" });
    await page.goto("/#session=fixture");
    await openProof(page);
    const graph = page.locator(".main-proof-graph");
    const idle = graph.getByRole("button", { name: /^Proof obligation 1,/ });
    const running = graph.getByRole("button", { name: /^Proof obligation 2,/ });
    const evidence = graph.getByRole("button", { name: /^reverseZeroAdd,/ });
    const activity = running.locator('.work[data-state="running"]');
    const spinner = activity.locator(".spin");

    await evidence.click();
    await page.mouse.move(8, 8);
    await expect(evidence).toHaveAttribute("aria-pressed", "true");
    await expect(running).toHaveAttribute("aria-pressed", "false");
    await expect(activity).toBeVisible();
    await expect(activity).toHaveText("2");
    await expect(activity).toHaveAttribute("title", "Codex · GPT-5 Codex ×2");
    await expect(evidence.locator('.work[data-state="running"]')).toHaveCount(0);
    await expect(spinner).toBeVisible();
    await expect(spinner).toHaveCSS("animation-name", "none");
    const selected = await edgeOf(evidence);
    const neutral = await edgeOf(idle);
    expect(selected.borderTopColor).not.toBe(neutral.borderTopColor);
    expect(selected.boxShadow).not.toBe("none");
    expect(await edgeOf(running)).toEqual(neutral);

    await running.hover();
    const hovered = await edgeOf(running);
    expect(hovered.borderTopColor).not.toBe(neutral.borderTopColor);
    expect(hovered.borderTopColor).not.toBe(selected.borderTopColor);
    expect(hovered.boxShadow).toBe(neutral.boxShadow);

    await page.mouse.move(8, 8);
    await page.keyboard.press("Tab");
    await running.focus();
    await expect(running).toHaveAttribute("aria-pressed", "false");
    await expect(running).toHaveCSS("outline-style", "solid");
    await expect(running).toHaveCSS("outline-offset", "2px");
    expect(await edgeOf(running)).toEqual(neutral);

    await running.press("Enter");
    await expect(running).toHaveAttribute("aria-pressed", "true");
    await expect(evidence).toHaveAttribute("aria-pressed", "false");
    expect(await edgeOf(running)).toEqual(selected);
    await expect(running).toHaveCSS("outline-style", "solid");
    await expect(activity).toHaveText("2");
    await expect(spinner).toBeVisible();
    await expect(spinner).toHaveCSS("animation-name", "none");

    await page.emulateMedia({ colorScheme, reducedMotion: "no-preference" });
    await expect(spinner).toHaveCSS("animation-name", "spin");
  });
}
