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
const context = await browser.newContext({
  viewport: { width: 1440, height: 1000 },
});
const page = await context.newPage();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const base = process.env.CHANCE_TEST_URL || "http://127.0.0.1:8766";
const state = () =>
  page.evaluate(() => fetch("/api/workspace").then((r) => r.json()));
try {
  for (const failList of [false, true]) {
    const cachePage = await browser.newPage();
    await cachePage.route("**/static/watchlist.mjs*", (route) =>
      failList || !new URL(route.request().url()).search
        ? route.fulfill({
            contentType: "text/javascript",
            body: "export function renderWatchlist() { throw Error('old or broken list'); }",
          })
        : route.continue(),
    );
    for (const refresh of [false, true]) {
      const dependency = cachePage.waitForResponse(/\/static\/watchlist\.mjs/);
      await (refresh ? cachePage.reload() : cachePage.goto(base));
      const entry = await cachePage.locator('script[src*="workspace.mjs"]').getAttribute("src");
      const version = new URL((await dependency).url()).search;
      assert.ok(version);
      assert.equal(version, new URL(entry, base).search);
      await cachePage.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
      await cachePage.locator(".indicator-catalog section > strong").first().waitFor();
      if (failList)
        await cachePage.locator("#watchlist-items").getByText("观察列表暂不可用，请刷新页面重试").waitFor();
      else await cachePage.locator(".watchlist-symbol").first().waitFor();
    }
    await cachePage.close();
  }
  const failedQuotePage = await browser.newPage();
  await failedQuotePage.route(base + "/", async (route) => {
    const response = await route.fetch();
    const html = (await response.text()).replace(
      /(<script id="chart-data" type="application\/json">)(.*?)(<\/script>)/s,
      (_, opening, data, closing) => {
        const items = JSON.parse(data).filter((item) => item.asset.id !== "nasdaq100");
        const item = items[0];
        item.last_quote = {
          bar_date: item.result.bar_date,
          close: item.result.close,
          fetched_at: item.fetched_at,
          is_provisional: 0,
        };
        item.status = "error";
        item.result = item.fetched_at = null;
        return opening + JSON.stringify(items).replaceAll("<", "\\u003c") + closing;
      },
    );
    await route.fulfill({ response, body: html });
  });
  for (const width of [1440, 390]) {
    await failedQuotePage.emulateMedia({ colorScheme: width === 1440 ? "light" : "dark" });
    await failedQuotePage.setViewportSize({ width, height: 1000 });
    await failedQuotePage.goto(base);
    const warning = failedQuotePage.locator("#chart-quote-warning");
    await warning.waitFor();
    assert.match(await warning.innerText(), /本次未能更新，当前显示 \d{4}-\d{2}-\d{2} 收盘数据/);
    assert.notEqual(await failedQuotePage.locator("#chart-price").innerText(), "—");
    assert.equal(await failedQuotePage.locator("#chart-sparkline").isVisible(), false);
    assert.equal(await failedQuotePage.locator(".workspace-data").getAttribute("open"), null);
    await failedQuotePage.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
    await failedQuotePage.locator(".indicator-catalog section > strong").first().waitFor();
  }
  await failedQuotePage.setViewportSize({ width: 1440, height: 1000 });
  await failedQuotePage.locator("#indicator-dialog [data-close]").click();
  let finishQuote;
  const pendingQuote = new Promise((resolve) => { finishQuote = resolve; });
  await failedQuotePage.route("**/api/workspace/quote", async (route) => {
    await pendingQuote;
    await route.fulfill({ status: 400, json: { detail: "合成行情不可用" } });
  });
  const freshSymbol = failedQuotePage.locator('[data-asset-id="nasdaq100"] .watchlist-symbol');
  const quoteRequested = failedQuotePage.waitForRequest("**/api/workspace/quote");
  await freshSymbol.click();
  await quoteRequested;
  assert.match(await failedQuotePage.locator("#chart-title").innerText(), /纳斯达克100/);
  assert.equal(await freshSymbol.getAttribute("aria-pressed"), "true");
  assert.equal(await failedQuotePage.locator("#chart-sparkline").isVisible(), false);
  finishQuote();
  await failedQuotePage.getByText("合成行情不可用").waitFor();
  await failedQuotePage.close();
  await page.goto(base);
  const reminder = page.locator('[data-open="pinned-note-dialog"]');
  assert.equal(await reminder.count(), 0);
  await page.getByRole("link", { name: "备忘录", exact: true }).click();
  await page.getByLabel("Markdown").fill("# 合成置顶提醒\n\n复盘前读一次。");
  await page.getByRole("button", { name: "保存备忘" }).click();
  await page.getByRole("button", { name: "置顶" }).click();
  await page.getByRole("link", { name: "标的", exact: true }).click();
  await page.waitForSelector('#chart[data-ready="true"]');
  await page.waitForSelector(".watchlist-symbol");
  const sparkline = page.locator("#chart-sparkline");
  await sparkline.waitFor();
  const initialPoints = await sparkline.locator("polyline").getAttribute("points");
  assert.equal(initialPoints.trim().split(/\s+/).length, 20);
  assert.equal(
    await sparkline.locator("polyline").evaluate((line) => getComputedStyle(line).animationName),
    "sparkline-draw",
  );
  assert.equal(await sparkline.locator("polyline").evaluate(async (line) => {
    const [animation] = line.getAnimations();
    await animation.finished;
    window.ChanceChart.show(window.ChanceChart.current());
    return line.getAnimations().length;
  }), 0, "redrawing the same symbol must not replay the entrance");
  const symbols = page.locator(".watchlist-symbol");
  await symbols.nth(1).evaluate(() => {
    for (const index of [1, 0, 1])
      document.querySelectorAll(".watchlist-symbol")[index].click();
  });
  assert.equal(
    await symbols.nth(1).getAttribute("aria-pressed"),
    "true",
    "the last choice wins without waiting for animation",
  );
  assert.ok((await page.locator("#chart-title").innerText()).includes(
    await symbols.nth(1).locator("strong").first().innerText(),
  ));
  await page.emulateMedia({ reducedMotion: "reduce" });
  assert.equal(
    await sparkline.locator("polyline").evaluate((line) => getComputedStyle(line).animationName),
    "none",
  );
  await symbols.first().click();
  assert.equal(await page.locator(".selection-moving").count(), 0);
  await page.emulateMedia({ reducedMotion: "no-preference" });
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    assert.notEqual(await page.locator("#chart-price").innerText(), "—");
    assert.match(await page.locator("#chart-quote-date").innerText(), /交易日 \d{4}-\d{2}-\d{2}/);
    assert.equal(await page.locator("#chart-quote-warning").isVisible(), false);
    await page.evaluate(() => new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve)),
    ));
    const dark = width === 390;
    await page.emulateMedia({ colorScheme: dark ? "dark" : "light" });
    await page.waitForFunction(
      (theme) => document.documentElement.dataset.theme === theme,
      dark ? "dark" : "light",
    );
    assert.equal(
      await page.locator("body").evaluate((element) => getComputedStyle(element).backgroundColor),
      dark ? "rgb(28, 27, 26)" : "rgb(247, 245, 240)",
    );
    const panelColors = await page.locator(".chart-panel").evaluate((element) => {
      const style = getComputedStyle(element);
      const luminance = (value) => {
        const channels = value.match(/\d+/g).slice(0, 3).map((n) => Number(n) / 255);
        return channels.map((n, i) =>
          (n <= 0.04045 ? n / 12.92 : ((n + 0.055) / 1.055) ** 2.4) *
          [0.2126, 0.7152, 0.0722][i],
        ).reduce((a, b) => a + b);
      };
      const contrast = (style) => {
        const [lighter, darker] = [style.color, style.backgroundColor]
          .map(luminance).sort((a, b) => b - a);
        return (lighter + 0.05) / (darker + 0.05);
      };
      const saveButton = document.querySelector(".decision-panel .save-button");
      return {
        background: style.backgroundColor,
        contrast: contrast(style),
        actionContrast: contrast(getComputedStyle(saveButton)),
      };
    });
    assert.equal(panelColors.background, dark ? "rgb(37, 35, 33)" : "rgb(252, 251, 249)");
    assert.ok(panelColors.contrast >= 4.5);
    assert.ok(panelColors.actionContrast >= 4.5, "the primary action must remain readable");
    const note = page.getByRole("button", { name: /置顶提醒 · 合成置顶提醒/ });
    await note.focus();
    await page.keyboard.press("Shift+Tab");
    await page.keyboard.press("Tab");
    assert.equal(await note.evaluate((node) =>
      node === document.activeElement && getComputedStyle(node).outlineStyle === "solid",
    ), true);
    const chartBeforeNote = await page.locator("#chart-title").innerText();
    const viewBeforeNote = await page.evaluate(() => window.ChanceChart.view());
    await note.click();
    await page.locator("#pinned-note-dialog[open]").waitFor();
    await page.getByText("复盘前读一次。").waitFor();
    await page.keyboard.press("Escape");
    await page.locator("#pinned-note-dialog").waitFor({ state: "hidden" });
    assert.equal(await note.evaluate((node) => document.activeElement === node), true);
    assert.equal(await page.locator("#chart-title").innerText(), chartBeforeNote);
    assert.deepEqual(await page.evaluate(() => window.ChanceChart.view()), viewBeforeNote);
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
  const selectedBeforeReopening = await page.locator("#chart-title").innerText();
  const reopened = await page.context().newPage();
  await reopened.setViewportSize({ width: 390, height: 900 });
  await reopened.goto(base);
  await reopened.waitForSelector('#chart[data-ready="true"]');
  assert.equal(await reopened.locator("#chart-title").innerText(), selectedBeforeReopening);
  const nav = reopened.getByRole("navigation", { name: "一级导航" });
  assert.deepEqual(await nav.getByRole("link").allTextContents(), ["标的", "指标", "备忘录"]);
  await nav.getByRole("link", { name: "指标" }).click();
  await reopened.getByRole("heading", { name: "指标库" }).waitFor();
  const libraryRule = reopened.locator("#strategy-macd");
  assert.ok(await libraryRule.getByText(/个标的/).isVisible());
  await libraryRule.getByRole("button", { name: "MACD 默认" }).click();
  await reopened.getByText(/影响范围：/).waitFor();
  await reopened.getByRole("button", { name: "关闭规则" }).click();
  await nav.getByRole("link", { name: "备忘录" }).click();
  await reopened.getByRole("heading", { name: "历史备忘" }).waitFor();
  const newNote = reopened.getByRole("link", { name: "新建备忘" });
  if (await newNote.count()) await newNote.click();
  await reopened.getByLabel("Markdown").fill("# 合成备忘");
  await reopened.getByRole("button", { name: "保存备忘" }).click();
  await reopened.getByRole("button", { name: "置顶" }).click();
  await reopened.getByRole("link", { name: "删除" }).click();
  await reopened.getByText("确定删除「合成备忘」？").waitFor();
  await reopened.getByRole("button", { name: "确认删除" }).click();
  assert.equal(await reopened.getByText("合成备忘", { exact: true }).count(), 0);
  await nav.getByRole("link", { name: "标的" }).click();
  await reopened.waitForSelector('#chart[data-ready="true"]');
  assert.equal(await reopened.locator("#chart-title").innerText(), selectedBeforeReopening);
  await reopened.close();
  const before = await state();
  await page.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
  const catalog = page.locator(".indicator-catalog");
  assert.equal(await catalog.locator("section > strong").count(), 9);
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
  assert.equal(await page.locator("dialog[open]").count(), 1);
  assert.equal(await catalog.isVisible(), true);
  await page.getByLabel("布林周期", { exact: true }).fill("30");
  await page.locator("#close-indicator-settings").click();
  await saved.click();
  await savedSettings.click();
  assert.equal(
    await page.getByLabel("布林周期", { exact: true }).inputValue(),
    "20",
  );
  await page.locator("#close-indicator-settings").click();
  await page
    .locator(".indicator-pill")
    .filter({ has: savedSettings })
    .getByRole("button", { name: "从图表移除" })
    .click();
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
  await page.locator("#close-indicator-settings").click();
  await page.reload();
  await page.waitForSelector("#exploration-panel:not([hidden])");
  await page.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
  await page.getByRole("button", { name: "布林突破 ⚙", exact: true }).click();
  assert.equal(
    await page.getByLabel("布林周期", { exact: true }).inputValue(),
    "30",
  );
  await page.locator("#close-indicator-settings").click();
  await page.locator("#indicator-dialog [data-close]").click();
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
  await page.locator('.chart-toolbar [data-open="indicator-dialog"]').click();
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
  await page.locator("#close-indicator-settings").click();
  await page.locator("#indicator-dialog [data-close]").click();
  await page.locator('.chart-toolbar [data-open="symbols-dialog"]').click();
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
