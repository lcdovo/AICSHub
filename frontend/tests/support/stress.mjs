import { createRequire } from "node:module";
import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import assert from "node:assert/strict";
const require = createRequire(
  process.env.PLAYWRIGHT_PACKAGE_ROOT + "/package.json",
);
const { chromium } = require("playwright");
const output = new URL(
  "../../../reports/frontend/after/stress/",
  import.meta.url,
);
await mkdir(output, { recursive: true });
const browser = await chromium.launch({ channel: "msedge", headless: true });
const context = await browser.newContext({
  viewport: { width: 360, height: 780 },
});
const page = await context.newPage();
const checked = [];
const shot = (name) =>
  page.screenshot({
    path: fileURLToPath(new URL(name + ".png", output)),
    fullPage: true,
  });
async function check(name, fn) {
  await fn();
  checked.push(name);
  console.log("PASS " + name);
}
try {
  await page.goto("http://127.0.0.1:8100/");
  await page.getByLabel("用户名", { exact: true }).fill("qa_customer");
  await page.getByLabel("密码", { exact: true }).fill("Local-QA-only-2026!");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.locator(".work-sheet").waitFor();
  await check("普通用户不能进入管理员路由或读取管理员列表", async () => {
    let adminRequests = 0;
    page.on("request", (request) => {
      if (request.url().includes("/api/v1/users/?")) adminRequests++;
    });
    await page.goto("http://127.0.0.1:8100/#/users");
    await page.getByRole("heading", { name: "无权访问" }).waitFor();
    assert.equal(adminRequests, 0);
    await shot("forbidden-360");
  });
  await page.goto("http://127.0.0.1:8100/#/chat");
  await page.getByRole("button", { name: "新建会话", exact: true }).click();
  await page.waitForURL(/#\/chat\/.+/);
  const id = page.url().split("/").pop();
  await check("空会话首屏和移动键盘缩放", async () => {
    await page.getByText("有什么可以帮你？").waitFor();
    await shot("empty-chat-360");
    await page.setViewportSize({ width: 360, height: 600 });
    await page.getByLabel("你的问题").focus();
    await page.waitForTimeout(100);
    const bottom = await page
      .getByLabel("你的问题")
      .evaluate((el) => el.getBoundingClientRect().bottom);
    assert.ok(bottom <= 600, "输入框应保持在可见区域");
    await shot("keyboard-resize-360");
  });
  await page.setViewportSize({ width: 360, height: 780 });
  await check("浏览器 WebSocket 无网络时保留明确错误，不自动重发", async () => {
    await context.setOffline(true);
    await page.getByLabel("你的问题").fill("合成断线测试");
    await page.getByRole("button", { name: "发送", exact: true }).click();
    await page.getByText("未能建立流式连接。", { exact: false }).waitFor();
    await shot("websocket-offline-360");
    await context.setOffline(false);
  });
  const longText =
    "很长的中文段落。".repeat(130) +
    "\n\n| 列名一 | 列名二 | 很长的说明 |\n| --- | --- | --- |\n| 内容一 | 内容二 | " +
    "不会越过页面边界的内容".repeat(20) +
    " |\n\n[长度很长的链接](https://example.com/" +
    "path/".repeat(60) +
    ")\n\n参考来源：\n- [合成验收资料](https://example.com/source)";
  await page.route(/\/api\/v1\/chat\/history\//, (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        code: 0,
        message: "QA fixture",
        data: {
          session_id: id,
          messages: [
            { id: 7001, role: "user", content: "长内容测试" },
            { id: 7002, role: "assistant", content: longText },
          ],
        },
      }),
    }),
  );
  await check("长中文、Markdown 表格、链接与引用局部滚动", async () => {
    await page.getByRole("button", { name: "刷新本轮记录" }).click();
    await page.getByText("查看参考来源").waitFor();
    const sizes = await page.evaluate(() => ({
      page: document.documentElement.scrollWidth,
      viewport: innerWidth,
      table: document.querySelector(".markdown-table")?.scrollWidth,
      tableViewport: document.querySelector(".markdown-table")?.clientWidth,
    }));
    assert.ok(sizes.page <= sizes.viewport + 1, JSON.stringify(sizes));
    assert.ok(sizes.table > sizes.tableViewport, "表格应在局部横向滚动");
    await page.locator(".message-scroll").evaluate((el) => {
      el.scrollTop = 0;
    });
    await shot("long-markdown-360");
  });
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await check("会话快速切换后迟到历史不污染当前会话", async () => {
    const access = await page.evaluate(() =>
      localStorage.getItem("cs_access_token"),
    );
    const make = async () => {
      const response = await page.request.post(
        "http://127.0.0.1:8100/api/v1/chat/sessions?user_id=101",
        { headers: { Authorization: "Bearer " + access } },
      );
      return (await response.json()).data.id;
    };
    const oldId = await make();
    const nextId = await make();
    const envelope = (session, content) => ({
      code: 0,
      message: "QA fixture",
      data: {
        session_id: session,
        messages: [
          {
            id: content === "A迟到历史" ? 8001 : 8002,
            role: "assistant",
            content,
          },
        ],
      },
    });
    await page.route(/\/api\/v1\/chat\/history\//, async (route) => {
      if (route.request().url().includes(oldId)) {
        await new Promise((resolve) => setTimeout(resolve, 1300));
        await route
          .fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify(envelope(oldId, "A迟到历史")),
          })
          .catch(() => {});
      } else
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(envelope(nextId, "B当前历史")),
        });
    });
    await page.goto("http://127.0.0.1:8100/#/chat/" + oldId);
    await page.goto("http://127.0.0.1:8100/#/chat/" + nextId);
    await page.getByText("B当前历史").waitFor();
    await page.waitForTimeout(1500);
    assert.equal(await page.getByText("A迟到历史").count(), 0);
  });
  await page.unrouteAll({ behavior: "ignoreErrors" });
  await check("列表加载和请求失败保持局部状态", async () => {
    await page.route(/\/api\/v1\/tickets\?/, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await route.fulfill({
        status: 503,
        contentType: "application/json",
        body: JSON.stringify({ detail: "合成工单服务不可用" }),
      });
    });
    await page.goto("http://127.0.0.1:8100/#/tickets");
    await page.getByText("正在加载…").waitFor();
    await shot("loading-360");
    await page.getByText("合成工单服务不可用").waitFor();
    await shot("tickets-error-360");
  });
} finally {
  await writeFile(
    new URL("checks.json", output),
    JSON.stringify(checked, null, 2),
  );
  await browser.close();
}
