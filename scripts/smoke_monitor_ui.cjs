/* Offline preview smoke test. Requires Playwright and an installed Edge. */
const { chromium } = require("playwright");
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");

(async () => {
  const base = process.env.PREVIEW_URL || "http://127.0.0.1:8766";
  const output = path.resolve(__dirname, "../data/theme-preview");
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ channel: "msedge", headless: true });
  const failures = [];
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  page.on("pageerror", error => failures.push(error.message));
  try {
    const login = await page.request.post(base + "/api/webui/login", { data: { secret: "preview-monitor-2026" } });
    assert.equal(login.status(), 200);
    const { token } = await login.json();
    await page.addInitScript(token => localStorage.setItem("apimonitorbot.webui.token", token), token);
    await page.goto(base, { waitUntil: "networkidle" });
    await page.getByRole("heading", { name: "监控管理", exact: true }).waitFor();
    await page.locator(".monitor-extensions summary", { hasText: "高级巡检设置" }).click();
    await page.getByLabel("请求超时（秒）", { exact: true }).fill("25");
    await page.getByRole("button", { name: "保存巡检设置" }).click();
    await page.getByRole("status").filter({ hasText: "巡检设置已保存" }).waitFor();
    await page.locator(".monitor-extensions summary", { hasText: "群聊情报" }).click();
    await page.getByLabel("监听群号", { exact: true }).fill("123456");
    await page.getByRole("button", { name: "保存情报设置" }).click();
    await page.getByRole("status").filter({ hasText: "群聊情报设置已保存" }).waitFor();
    await page.locator(".monitor-extensions summary", { hasText: "公开状态页" }).click();
    await page.getByRole("button", { name: "保存公开设置" }).click();
    await page.getByRole("status").filter({ hasText: "公开状态页设置已保存" }).waitFor();
    await page.locator(".monitor-extensions").screenshot({ path: path.join(output, "web-desktop.png") });
    assert.equal(await page.locator(".monitor-latency").count(), 6);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.setViewportSize({ width: 390, height: 844 });
    await page.locator(".monitor-extensions").screenshot({ path: path.join(output, "web-mobile.png") });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    const guest = await browser.newPage({ viewport: { width: 390, height: 844 } });
    guest.on("pageerror", error => failures.push(error.message));
    await guest.goto(base + "/status", { waitUntil: "networkidle" });
    await guest.getByText("Plus 标准分组", { exact: false }).waitFor();
    await guest.screenshot({ path: path.join(output, "public-mobile.png"), fullPage: true });
    assert.equal((await guest.request.get(base + "/api/intelligence/findings")).status(), 401);
    assert.equal((await guest.request.get(base + "/api/public-status")).status(), 200);
    assert.equal(await guest.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    assert.deepEqual(failures, []);
    console.log("Desktop/mobile settings, latency charts, public view, auth boundary: PASS");
  } catch (error) {
    await page.screenshot({ path: path.join(output, "web-failure.png"), fullPage: true });
    console.error("Page errors:", failures);
    console.error("Overflow:", await page.evaluate(() => [...document.querySelectorAll("body *")].filter(e => {
      const r = e.getBoundingClientRect(); return r.width > 0 && r.right > innerWidth + 1 && getComputedStyle(e).position !== "fixed";
    }).slice(0, 25).map(e => ({ tag: e.tagName, class: e.className, right: e.getBoundingClientRect().right, width: e.getBoundingClientRect().width }))));
    console.error("Details:", await page.locator(".monitor-extensions details").evaluateAll(nodes => nodes.map(n => ({ text: n.querySelector("summary")?.textContent, open: n.open }))));
    throw error;
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
