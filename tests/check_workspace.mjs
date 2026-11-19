// Optional integration check: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/check_workspace.mjs
// Run against an isolated database on CHANCE_TEST_URL (default :8766); this check saves test templates/configuration.
import assert from "node:assert/strict";
const { chromium } = await import(
  process.env.PLAYWRIGHT_MODULE || "playwright"
);
const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.CHROME_BIN ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
const page = await browser.newPage({
  viewport: { width: 1440, height: 1000 },
});
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const base = process.env.CHANCE_TEST_URL || "http://127.0.0.1:8766";
const state = () =>
  page.evaluate(() => fetch("/api/workspace").then((r) => r.json()));
try {
  await page.goto(base);
  await page.waitForSelector('#chart[data-ready="true"]');
  await page.waitForSelector(".watchlist-symbol");
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    const assetId = width === 1440 ? "nasdaq100" : "sp500";
    if (width === 390) {
      await page.locator("#watchlist-panel").waitFor({ state: "hidden" });
      assert.ok((await page.locator("#chart").boundingBox()).y < 844);
      assert.match(
        await page.locator("#ohlc").innerText(),
        /开[\s\S]*高[\s\S]*低[\s\S]*[收现]/,
      );
      await page.locator("#toggle-watchlist").click();
    }
    await page
      .locator(".watchlist-symbol")
      .filter({ hasText: width === 1440 ? "纳斯达克100" : "标普500" })
      .click();
    await page.locator('[data-layout="signals"]').evaluate((panel) => {
      panel.open = false;
    });
    await page.locator('[data-layout="journal"]').evaluate((panel) => {
      panel.open = true;
    });
    await page.getByRole("button", { name: "前往记录判断" }).click();
    assert.equal(
      await page
        .locator(`[data-detail="${assetId}"] textarea[name="note"]`)
        .evaluate((note) => document.activeElement === note),
      true,
    );
  }
  await page.locator('[data-detail="sp500"]').evaluate((panel) => {
    panel.hidden = true;
  });
  await page.getByRole("button", { name: "前往记录判断" }).click();
  assert.match(
    await page.locator("#journal-record-status").innerText(),
    /当前标的没有正式报告/,
  );
  await page.locator('[data-detail="sp500"]').evaluate((panel) => {
    panel.hidden = false;
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const before = await state();
  await page.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
  const catalog = page.locator(".indicator-catalog");
  assert.equal(await catalog.locator("section > strong").count(), 4);
  assert.match(
    await catalog.innerText(),
    /MACD · 副图 · 快线 12 \/ 慢线 26 \/ 信号线 9/,
  );
  assert.match(await catalog.innerText(), /布林回归 · 从轨外回到轨内/);
  const bollinger = catalog.locator("section").filter({ hasText: "突破轨道" });
  await bollinger.getByText("已存配置", { exact: true }).click();
  assert.match(
    await bollinger.innerText(),
    /周期 20 \/ 标准差倍数 2 · 与默认参数相同/,
  );
  const saved = bollinger.getByRole("button", { name: /布林突破 20\/2 ·/ });
  await saved.click();
  const savedSettings = page.getByRole("button", {
    name: "布林突破 20/2 ⚙",
    exact: true,
  });
  await savedSettings.click();
  await page.getByLabel("布林周期", { exact: true }).fill("30");
  await page.locator("#indicator-settings [data-close]").click();
  await page.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
  await saved.click();
  await savedSettings.click();
  assert.equal(
    await page.getByLabel("布林周期", { exact: true }).inputValue(),
    "20",
  );
  await page.locator("#indicator-settings [data-close]").click();
  await page
    .locator(".indicator-pill")
    .filter({ has: savedSettings })
    .getByRole("button", { name: "×" })
    .click();
  await page.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
  assert.equal(
    await catalog.getByRole("button", { name: "创建组合策略…" }).count(),
    1,
  );
  await page.getByRole("button", { name: "布林突破 ＋", exact: true }).click();
  await page.waitForSelector("#exploration-panel:not([hidden])");
  assert.equal(
    (await state()).configuration.version,
    before.configuration.version,
  );
  assert.equal(
    await page.locator(".macd-legend").isVisible(),
    false,
    "Bollinger-only exploration must not show an unselected MACD",
  );
  await page.getByRole("button", { name: "布林突破 ⚙", exact: true }).click();
  await page.getByLabel("布林周期", { exact: true }).fill("30");
  await page.waitForFunction(() =>
    document
      .getElementById("workspace-status")
      .textContent.includes("探索草稿已保留"),
  );
  await page.locator("#indicator-settings [data-close]").click();
  await page.reload();
  await page.waitForSelector("#exploration-panel:not([hidden])");
  await page.getByRole("button", { name: "布林突破 ⚙", exact: true }).click();
  assert.equal(
    await page.getByLabel("布林周期", { exact: true }).inputValue(),
    "30",
  );
  await page.locator("#indicator-settings [data-close]").click();
  await page.locator('.chart-toolbar [data-open="templates-dialog"]').click();
  await page.getByLabel("模板名称").fill("工作台集成测试");
  await page.getByRole("button", { name: "另存为新模板", exact: true }).click();
  await page.waitForFunction(() =>
    document
      .querySelector("#templates-dialog .dialog-status")
      .textContent.includes("模板已保存"),
  );
  assert.equal(
    (await state()).configuration.version,
    before.configuration.version,
  );
  await page.locator("#templates-dialog [data-close]").click();
  await page
    .locator(".watchlist-symbol")
    .filter({ hasText: "纳斯达克100" })
    .click();
  await page.waitForFunction(() =>
    document.getElementById("chart-title").textContent.includes("纳斯达克100"),
  );
  await page.waitForSelector("#exploration-panel:not([hidden])");
  assert.ok(
    (await page.locator("#indicator-strip").innerText()).includes("布林突破"),
  );
  await page.getByRole("button", { name: "布林突破 ⚙", exact: true }).click();
  await page
    .getByRole("button", { name: "加入 / 更新监测", exact: true })
    .click();
  await page.getByRole("button", { name: "保存监测规则", exact: true }).click();
  await page.waitForFunction(() =>
    document
      .querySelector("#monitor-dialog .dialog-status")
      .textContent.includes("已保存"),
  );
  const after = await state();
  assert.equal(after.configuration.version, before.configuration.version + 1);
  assert.deepEqual(
    after.configuration.assets.find((a) => a.id === "sp500").strategies,
    before.configuration.assets.find((a) => a.id === "sp500").strategies,
  );
  assert.equal(
    after.configuration.assets.find((a) => a.id === "nasdaq100").strategies
      .length,
    before.configuration.assets.find((a) => a.id === "nasdaq100").strategies
      .length + 1,
  );
  await page.locator("#monitor-dialog [data-close]").click();
  await page.locator("#indicator-settings [data-close]").click();
  await page.locator('.workspace-header [data-open="symbols-dialog"]').click();
  await page.getByLabel("名称 / 代码").fill("SOX");
  await page.waitForFunction(() =>
    document
      .getElementById("symbol-results")
      .textContent.includes("费城半导体"),
  );
  await page.locator("#symbols-dialog [data-close]").click();
  // Decision drafts survive a reload; exploration evidence is saved separately.
  page.on("dialog", (dialog) => dialog.accept());
  let form = page.locator(".asset-detail:not([hidden]) .decision-panel form");
  await form.locator('[name="note"]').fill("工作台决策草稿恢复测试");
  await page.reload();
  await page.waitForSelector("#exploration-panel:not([hidden])");
  form = page.locator(".asset-detail:not([hidden]) .decision-panel form");
  assert.equal(
    await form.locator('[name="note"]').inputValue(),
    "工作台决策草稿恢复测试",
  );
  await form.locator("[data-basis-choice]").selectOption("exploration");
  await Promise.all([
    page.waitForURL("**/reports/*"),
    form.locator(".save-button").click(),
  ]);
  await page.waitForLoadState("networkidle");
  assert.ok(
    (await page.locator("#journal").textContent()).includes(
      "工作台决策草稿恢复测试",
    ),
  );
  const evidence = await page
    .locator('a[href^="/api/decisions/"]')
    .last()
    .getAttribute("href");
  assert.equal(
    (await (await page.request.get(base + evidence)).json()).mode,
    "exploration",
  );
  await page.screenshot({
    path: "/tmp/chance-workspace-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator("#toggle-watchlist").click();
  await page
    .locator(".watchlist-symbol")
    .filter({ hasText: "标普500" })
    .click();
  const official = page.locator('[data-detail="sp500"]:not([hidden])');
  await official
    .getByRole("heading", { name: "最新日线信号" })
    .scrollIntoViewIfNeeded();
  const officialForm = official.locator("form");
  await officialForm.locator("[data-basis-choice]").selectOption("report");
  await officialForm.locator('[name="note"]').fill("窄屏正式判断验收");
  await Promise.all([
    page.waitForNavigation(),
    officialForm.locator(".save-button").click(),
  ]);
  const officialEntry = await page
    .locator("#journal tr")
    .filter({ hasText: "窄屏正式判断验收" })
    .textContent();
  assert.match(officialEntry, /标普500/);
  assert.match(officialEntry, /原始报告依据/);
  const release = Promise.withResolvers();
  await page.route("**/api/workspace/preview", async (route) => {
    if (route.request().postDataJSON().asset_id !== "sp500")
      return route.continue();
    const response = await route.fetch();
    await release.promise;
    await route.fulfill({ response });
  });
  const pending = page.waitForRequest(
    (request) =>
      request.url().endsWith("/api/workspace/preview") &&
      request.postDataJSON().asset_id === "sp500",
  );
  await page.evaluate(() => {
    document.querySelector(".watchlist-symbol[aria-pressed=true]").click();
  });
  await pending;
  await page.locator("#toggle-watchlist").click();
  await page
    .locator(".watchlist-symbol")
    .filter({ hasText: "纳斯达克100" })
    .click();
  await page.waitForSelector("#exploration-panel:not([hidden])");
  const currentPreview = await page.locator("#exploration-panel").innerText();
  const late = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/workspace/preview") &&
      response.request().postDataJSON().asset_id === "sp500",
  );
  release.resolve();
  await (await late).finished();
  await page.waitForLoadState("networkidle");
  assert.match(await page.locator("#chart-title").innerText(), /纳斯达克100/);
  assert.equal(
    await page.locator("#exploration-panel").innerText(),
    currentPreview,
  );
  await page.screenshot({
    path: "/tmp/chance-workspace-mobile.png",
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > innerWidth,
    ),
    false,
    "mobile must not overflow",
  );
  await page.goto(base + "/reports/11");
  await page.waitForSelector('#chart[data-ready="true"]');
  assert.ok(
    (await page.locator("#workspace-mode").innerText()).includes("历史报告"),
  );
  assert.equal(
    await page.locator("#exploration-panel").isVisible(),
    false,
    "history defaults to original results",
  );
  assert.deepEqual(errors, []);
  console.log(
    "PASS: chart rendering, inline indicators, parameter/decision draft restore, template reuse, private monitoring, evidence, historical defaults and mobile overflow",
  );
} catch (error) {
  console.error("page errors", errors);
  console.error("status", await page.locator("#workspace-status").innerText());
  await page.screenshot({
    path: "/tmp/chance-workspace-failure.png",
    fullPage: true,
  });
  throw error;
} finally {
  await browser.close();
}
