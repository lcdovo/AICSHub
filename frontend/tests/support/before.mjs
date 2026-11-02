import { createRequire } from "node:module";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
const require = createRequire(
  process.env.PLAYWRIGHT_PACKAGE_ROOT + "/package.json",
);
const { chromium } = require("playwright");
const browser = await chromium.launch({ channel: "msedge", headless: true });
const out = new URL("../../../reports/frontend/before/", import.meta.url);
await mkdir(out, { recursive: true });
for (const role of ["user", "admin"]) {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto("http://127.0.0.1:8100/");
  if (role === "user")
    await page.screenshot({
      path: fileURLToPath(new URL("login-1440.png", out)),
      fullPage: true,
    });
  await page
    .locator("input")
    .first()
    .fill(role === "user" ? "qa_customer" : "qa_admin");
  await page.locator("input[type=password]").fill("Local-QA-only-2026!");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await page.locator(".shell").waitFor();
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.screenshot({
      path: fileURLToPath(new URL(`${role}-${width}.png`, out)),
      fullPage: true,
    });
  }
  await context.close();
}
await browser.close();
console.log("Saved baseline login/user/admin desktop and mobile screenshots.");
