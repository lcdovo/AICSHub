import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const require = createRequire(
  process.env.PLAYWRIGHT_PACKAGE_ROOT + "/package.json",
);
const { chromium } = require("playwright");
const out = new URL("../../../reports/frontend/after/", import.meta.url);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const errors = [];
const results = [];
async function check(name, fn) {
  try {
    await fn();
    results.push({ name, passed: true });
    console.log("PASS " + name);
  } catch (e) {
    results.push({ name, passed: false, error: e.message });
    console.log("FAIL " + name + ": " + e.message);
    throw e;
  }
}
async function screenshot(page, name) {
  await page.screenshot({
    path: fileURLToPath(new URL(name + ".png", out)),
    fullPage: true,
  });
}
async function login(page, role) {
  await page.goto("http://127.0.0.1:8100/");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(role === "user" ? "qa_customer" : "qa_admin");
  await page.getByLabel("密码", { exact: true }).fill("Local-QA-only-2026!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator(".work-sheet").waitFor();
}
async function go(page, route) {
  await page.goto("http://127.0.0.1:8100/#/" + route);
  await page.locator(".work-sheet h1").waitFor();
  await page.waitForLoadState("networkidle");
}
async function overflow(page) {
  const result = await page.evaluate(() => ({
    body: document.documentElement.scrollWidth,
    width: innerWidth,
    sheet: document.querySelector(".work-sheet")?.getBoundingClientRect().right,
  }));
  assert.ok(result.body <= result.width + 1, JSON.stringify(result));
}
const userContext = await browser.newContext({
  permissions: ["clipboard-read", "clipboard-write"],
  viewport: { width: 1440, height: 900 },
});
const user = await userContext.newPage();
user.on("pageerror", (e) => errors.push(e.message));
let sessionId, secondId, ticketId;
try {
  await user.goto("http://127.0.0.1:8100/");
  for (const width of [1440, 1280, 768, 390, 360]) {
    await user.setViewportSize({ width, height: width < 500 ? 844 : 900 });
    await screenshot(user, "login-" + width);
    await overflow(user);
  }
  await user.setViewportSize({ width: 1440, height: 900 });
  await check("普通用户真实登录", () => login(user, "user"));
  await check("新建会话与 IME 输入", async () => {
    await user.getByRole("button", { name: "新建会话", exact: true }).click();
    await user.waitForURL(/#\/chat\/.+/);
    sessionId = user.url().split("/").pop();
    const input = user.getByLabel("你的问题");
    await input.fill("你好");
    await input.dispatchEvent("keydown", {
      key: "Enter",
      code: "Enter",
      isComposing: true,
      keyCode: 229,
    });
    assert.equal(await input.inputValue(), "你好");
  });
  await check("真实 WebSocket 流式回答与持久化 ID", async () => {
    await user.getByRole("button", { name: "发送", exact: true }).click();
    await user
      .getByRole("button", { name: "有帮助", exact: true })
      .waitFor({ timeout: 30000 });
    assert.ok((await user.locator(".message.assistant").count()) > 0);
  });
  await check("复制及真实反馈", async () => {
    await user
      .getByRole("button", { name: "复制", exact: true })
      .first()
      .click();
    await user.getByRole("button", { name: "已复制", exact: true }).waitFor();
    await user
      .getByRole("button", { name: "有帮助", exact: true })
      .first()
      .click();
    await user.getByRole("button", { name: "已反馈", exact: true }).waitFor();
  });
  await check("转人工确认及服务端回执", async () => {
    await user.getByRole("button", { name: "转人工", exact: true }).click();
    await user
      .getByLabel("需要人工协助的原因")
      .fill("前端验收：合成问题需要人工协助");
    await user.getByRole("button", { name: "确认提交人工请求" }).click();
    await user.getByRole("heading", { name: "人工请求已提交" }).waitFor();
    await user.getByRole("button", { name: "知道了" }).click();
  });
  for (const width of [1440, 1280, 768, 390, 360]) {
    await user.setViewportSize({ width, height: width < 500 ? 844 : 900 });
    await screenshot(user, "user-chat-" + width);
    await overflow(user);
  }
  await user.setViewportSize({ width: 1440, height: 900 });
  await check("快速切换会话与独立草稿", async () => {
    await user.getByRole("button", { name: "新建会话", exact: true }).click();
    await user.waitForURL((url) => !url.hash.endsWith(sessionId));
    secondId = user.url().split("/").pop();
    await user.getByLabel("你的问题").fill("独立草稿 B");
    await user.goto("http://127.0.0.1:8100/#/chat/" + sessionId);
    await user.getByLabel("你的问题").waitFor();
    assert.notEqual(
      await user.getByLabel("你的问题").inputValue(),
      "独立草稿 B",
    );
    await user.goto("http://127.0.0.1:8100/#/chat/" + secondId);
    await user.waitForTimeout(200);
    assert.equal(await user.getByLabel("你的问题").inputValue(), "独立草稿 B");
  });
  await check("普通请求传输保留", async () => {
    await user.getByLabel("消息传输方式").selectOption("rest");
    await user.getByLabel("你的问题").fill("你好");
    await user.getByRole("button", { name: "发送", exact: true }).click();
    await user
      .getByRole("button", { name: "有帮助", exact: true })
      .waitFor({ timeout: 30000 });
  });
  await check("真实创建工单、列表与详情", async () => {
    await go(user, "tickets");
    await user.getByRole("button", { name: "创建工单", exact: true }).click();
    await user
      .getByLabel("问题描述")
      .fill("前端验收：测试售后问题，请保留上下文。");
    await user.getByRole("button", { name: "确认创建工单" }).click();
    await user.getByRole("dialog").waitFor({ state: "hidden" });
    await user
      .getByRole("link", { name: "查看详情", exact: true })
      .first()
      .click();
    await user.getByRole("heading", { name: "工单详情" }).waitFor();
    ticketId = user.url().split("/").pop();
  });
  await check("移动导航与工单重组", async () => {
    await user.setViewportSize({ width: 360, height: 780 });
    await go(user, "tickets");
    await screenshot(user, "user-tickets-360");
    await overflow(user);
    await user.getByRole("button", { name: "打开导航" }).click();
    await user
      .getByRole("dialog")
      .getByRole("link", { name: "会话历史" })
      .click();
    await user
      .getByRole("heading", { name: "会话历史", exact: true })
      .waitFor();
  });
  const adminContext = await browser.newContext({
    viewport: { width: 1440, height: 900 },
  });
  const admin = await adminContext.newPage();
  admin.on("pageerror", (e) => errors.push(e.message));
  await check("管理员真实登录和其他用户会话只读", async () => {
    await login(admin, "admin");
    await go(admin, "chat/" + sessionId);
    await admin
      .getByText("其他用户的会话只读。新建测试会话后可以测试 Agent。")
      .waitFor();
    assert.equal(await admin.getByLabel("你的问题").count(), 0);
  });
  await check("管理员工单状态转换", async () => {
    await go(admin, "tickets/" + ticketId);
    await admin.getByRole("button", { name: "编辑处理状态" }).click();
    await admin.getByLabel("处理状态").selectOption("processing");
    await admin.getByLabel("负责人标识").fill("qa_agent");
    await admin.getByRole("button", { name: "确认保存处理" }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
    await admin.getByRole("button", { name: "编辑处理状态" }).click();
    await admin.getByLabel("处理状态").selectOption("resolved");
    await admin.getByLabel("处理结果").fill("合成验收已完成处理");
    await admin.getByRole("button", { name: "确认保存处理" }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
    await admin.getByText("合成验收已完成处理").waitFor();
  });
  await check("人工分配与完成", async () => {
    await go(admin, "handoff");
    await admin
      .getByRole("button", { name: "分配客服", exact: true })
      .first()
      .click();
    const select = admin.getByLabel("处理客服");
    const value = await select.locator("option").nth(1).getAttribute("value");
    await select.selectOption(value);
    await admin.getByRole("button", { name: "确认分配", exact: true }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
    await admin.getByRole("button", { name: "处理中", exact: true }).click();
    await admin
      .getByRole("button", { name: "处理完成", exact: true })
      .first()
      .click();
    await admin.getByLabel("处理结果").fill("合成请求处理完成");
    await admin.getByRole("button", { name: "确认完成", exact: true }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
  });
  await check("真实新增知识文档并检索", async () => {
    await go(admin, "knowledge");
    await admin.getByRole("button", { name: "新增文档", exact: true }).click();
    await admin.getByLabel("文档标题").fill("前端验收服务说明");
    await admin.getByLabel("分类", { exact: true }).fill("technical");
    await admin.getByLabel("来源", { exact: true }).fill("合成验收资料");
    await admin
      .getByLabel("知识正文")
      .fill(
        "前端验收服务说明：测试产品在七天内可凭订单申请售后。请提供订单号和具体故障，人工团队会核对处理。",
      );
    await admin.getByRole("button", { name: "确认保存并发布" }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
    await admin
      .getByRole("button", { name: "前端验收服务说明", exact: true })
      .first()
      .waitFor();
    await go(admin, "search");
    await admin.getByLabel("测试问题").fill("前端验收服务说明");
    await admin.getByRole("button", { name: "检索知识", exact: true }).click();
    await admin.getByRole("heading", { name: "命中内容" }).waitFor();
    await screenshot(admin, "admin-search-1440");
  });
  await check("运行分析与依赖降级", async () => {
    await go(admin, "analytics");
    await admin.getByRole("heading", { name: "依赖与服务状态" }).waitFor();
    await admin.getByText("未配置 LLM_API_KEY", { exact: false }).waitFor();
    await screenshot(admin, "admin-analytics-1440");
  });
  await check("用户创建、编辑与停用确认", async () => {
    await go(admin, "users");
    await admin.getByRole("button", { name: "创建用户", exact: true }).click();
    const newUsername = "qa_new_" + Date.now();
    await admin.getByLabel("用户名", { exact: true }).fill(newUsername);
    await admin.getByLabel("昵称", { exact: true }).fill("新增验收用户");
    await admin.getByLabel("初始密码").fill("Synthetic-user-2026!");
    await admin.getByRole("button", { name: "确认创建用户" }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
    const row = admin.getByRole("row").filter({ hasText: newUsername });
    await row.getByRole("button", { name: "编辑", exact: true }).click();
    await admin.getByLabel("昵称", { exact: true }).fill("已编辑验收用户");
    await admin.getByRole("button", { name: "保存资料" }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
    const edited = admin.getByRole("row").filter({ hasText: newUsername });
    await edited.getByRole("button", { name: "停用", exact: true }).click();
    await admin.getByRole("button", { name: "确认停用", exact: true }).click();
    await admin.getByRole("dialog").waitFor({ state: "hidden" });
    await edited.getByText("已停用", { exact: true }).waitFor();
  });
  for (const route of [
    "chat/" + sessionId,
    "tickets",
    "handoff",
    "knowledge",
    "search",
    "analytics",
    "users",
    "account",
  ]) {
    for (const width of [1440, 1280, 768, 390, 360]) {
      await admin.setViewportSize({ width, height: width < 500 ? 844 : 900 });
      await go(admin, route);
      await overflow(admin);
      await screenshot(admin, "admin-" + route.split("/")[0] + "-" + width);
    }
  }
  await check("局部指标失败保留其他成功数据", async () => {
    await admin.route(/\/api\/v1\/analytics\/metrics$/, (route) =>
      route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "合成故障：指标读取失败" }),
      }),
    );
    await go(admin, "analytics");
    await admin.getByRole("button", { name: "刷新数据" }).click();
    await admin.getByText("合成故障：指标读取失败").waitFor();
    assert.ok(await admin.locator(".metric-strip").innerText());
    await admin.getByRole("heading", { name: "依赖与服务状态" }).waitFor();
    await screenshot(admin, "partial-failure-360");
    await admin.unroute(/\/api\/v1\/analytics\/metrics$/);
  });
  await check("403 与 429 明确错误", async () => {
    for (const code of [403, 429]) {
      await admin.route("**/api/v1/users/?*", (route) =>
        route.fulfill({
          status: code,
          headers: { "Retry-After": "3" },
          contentType: "application/json",
          body: "{}",
        }),
      );
      await go(admin, "users");
      await admin.getByRole("button", { name: "刷新", exact: true }).click();
      await admin
        .getByText(
          code === 403
            ? "没有访问这项资源的权限。"
            : "操作过于频繁，请等待 3 秒后再试。",
          { exact: true },
        )
        .waitFor();
      await admin.unroute("**/api/v1/users/?*");
    }
  });
  await check("登录过期处理", async () => {
    await user.route("**/api/v1/users/**", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: "{}",
      }),
    );
    await go(user, "account");
    await user.getByText("登录已过期，请重新登录后继续。").waitFor();
    await screenshot(user, "expired-360");
  });
  await adminContext.close();
} catch (e) {
  await screenshot(user, "failure-user");
  throw e;
} finally {
  await writeFile(
    new URL("acceptance.json", out),
    JSON.stringify({ results, consoleErrors: errors }, null, 2),
  );
  await browser.close();
}
assert.equal(errors.length, 0, JSON.stringify(errors));
console.log(
  "Browser acceptance complete: " +
    results.length +
    " checks, no runtime exceptions.",
);
