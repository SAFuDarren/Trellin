// Regenerates the README screenshots from the Fermat's Last Theorem sample project
// (tests/fixtures/workbench_demo_fermat.py). Build the interface first, then:
//   npx pnpm@9.15.4 screenshots          (set PW_CHANNEL=chrome to use an installed Chrome)
import { spawn } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { chromium } from "@playwright/test";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const OUT = path.join(ROOT, "docs", "images");
const PORT = 4176;
const BASE = `http://127.0.0.1:${PORT}`;
const LAYOUT = { side: 264, right: 380, chat: 300, atlas: { x: 16, y: 16, w: 200 } };

async function waitForServer() {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      if ((await fetch(BASE + "/")).ok) return;
    } catch {
      // Not listening yet.
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw new Error("The demo server did not start.");
}

try {
  await fetch(BASE + "/");
  throw new Error(`Port ${PORT} is already in use; stop that server so the screenshots use the current demo data.`);
} catch (error) {
  if (error instanceof Error && error.message.startsWith("Port")) throw error;
}

const server = spawn("python3", [path.join(ROOT, "tests/fixtures/workbench_demo_fermat.py"), "--port", String(PORT)], {
  stdio: "ignore",
});
const browser = await chromium.launch({ channel: process.env.PW_CHANNEL });
try {
  await waitForServer();
  await mkdir(OUT, { recursive: true });
  for (const scheme of ["light", "dark"]) {
    const context = await browser.newContext({
      viewport: { width: 1600, height: 1000 },
      deviceScaleFactor: 1.5,
      colorScheme: scheme,
    });
    await context.addInitScript((layout) => {
      window.localStorage.setItem("research-multiplier-workbench-layout", JSON.stringify(layout));
      window.localStorage.setItem("research-multiplier-language", "en");
    }, LAYOUT);
    const page = await context.newPage();
    await page.goto(BASE + "/#session=demo");
    await page.locator(".graph-node").nth(9).waitFor();
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, `workbench-${scheme}.png`) });

    await page.getByRole("button", { name: "If withdrawn" }).click();
    await page.locator(".queue-item").first().waitFor();
    await page.waitForTimeout(800);
    await page.screenshot({ path: path.join(OUT, `withdrawal-${scheme}.png`) });
    await context.close();
  }
  console.log(`Screenshots written to ${path.relative(ROOT, OUT)}/`);
} finally {
  await browser.close();
  server.kill();
}
