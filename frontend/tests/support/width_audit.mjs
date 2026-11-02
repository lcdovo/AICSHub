import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const require = createRequire(
  process.env.PLAYWRIGHT_PACKAGE_ROOT + "/package.json",
);
const { chromium } = require("playwright");
const out = new URL(
  "../../../reports/frontend/after/width-audit/",
  import.meta.url,
);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const results = [];
try {
  for (const role of ["user", "admin"]) {
    const page = await browser.newPage();
    const port = role === "user" ? 8175 : 8174;
    const routes =
      role === "user"
        ? ["chat", "history", "tickets", "account"]
        : [
            "chat",
            "tickets",
            "handoff",
            "knowledge",
            "search",
            "analytics",
            "users",
            "account",
          ];
    for (const route of routes) {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(`http://127.0.0.1:${port}/?role=${role}#/${route}`);
      await page.locator(".work-sheet h1").waitFor();
      await page.waitForLoadState("networkidle");
      await page
        .locator(".loading")
        .first()
        .waitFor({ state: "hidden", timeout: 15000 })
        .catch(() => {});
      for (const width of [1440, 1280, 768, 390, 360]) {
        await page.setViewportSize({ width, height: width < 500 ? 844 : 900 });
        const sizes = await page.evaluate(() => ({
          document: document.documentElement.scrollWidth,
          viewport: innerWidth,
        }));
        assert.ok(
          sizes.document <= sizes.viewport + 1,
          `${role}/${route}/${width}: ${JSON.stringify(sizes)}`,
        );
        await page.screenshot({
          path: fileURLToPath(new URL(`${role}-${route}-${width}.png`, out)),
          fullPage: true,
        });
        results.push(`${role}/${route}/${width}`);
      }
    }
    await page.close();
  }
} finally {
  await writeFile(new URL("audit.json", out), JSON.stringify(results, null, 2));
  await browser.close();
}
console.log(
  `PASS ${results.length} route-width screenshots without page overflow.`,
);
