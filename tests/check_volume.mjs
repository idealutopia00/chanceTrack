// Run against the isolated workspace fixture in docs/development.md.
import assert from "node:assert/strict";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
try {
  const page = await browser.newPage({ hasTouch: true, viewport: { width: 390, height: 844 } });
  await page.route("**/static/app.js?*", async (route) => {
    const response = await route.fetch();
    await route.fulfill({
      response,
      body: (await response.text()).replace("chart.subscribeClick((event) => {",
        "window.volumeTest = { volumeBars, chart }; chart.subscribeClick((event) => {"),
    });
  });
  await page.goto(process.env.CHANCE_TEST_URL || "http://127.0.0.1:8766");
  await page.waitForSelector('#chart[data-ready="true"]');
  await page.evaluate(() => {
    const item = structuredClone(ChanceChart.current());
    const content = item.result.chart;
    const days = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13",
      "2026-11-16", "2026-11-17"];
    content.candles = days.map((time, index) => ({
      time, open: 100, high: 102, low: 98, close: 101,
      volume: [10, 0, 200000, 10, 10, undefined, 0][index],
    }));
    content.macd = days.map((time) => ({ time, dif: 1, dea: 0.5, histogram: 1 }));
    content.macd_configs = [{ id: "test-macd", name: "MACD", points: content.macd }];
    content.volume_unit = "千克";
    content.complete_volume_weeks = [days[4]];
    item.result.bar_date = days.at(-1);
    item.result.is_provisional = true;
    ChanceChart.show(item, { view: { from: 0, to: 7 } });
  });
  const toggle = page.getByRole("checkbox", { name: "成交量（VOL）" });
  await toggle.check();
  const series = () => page.evaluate(() => volumeTest.volumeBars.data().map(({ value }) => value ?? null));
  assert.deepEqual(await series(), [10, 0, 200000, 10, 10, 0]);
  assert.match(await page.locator("#ohlc").innerText(), /VOL 0.*千克.*截至当前累计/);
  const pane = page.locator("#chart tr").filter({ has: page.locator("canvas") }).first();
  await pane.scrollIntoViewIfNeeded();
  const point = await page.evaluate(async () => {
    await new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve)));
    const pane = [...document.querySelectorAll("#chart tr")].find((row) => row.children.length === 3);
    const bounds = pane.children[1].getBoundingClientRect();
    return {
      x: bounds.x + volumeTest.chart.timeScale().timeToCoordinate("2026-11-16"),
      y: Math.max(bounds.y + 120, document.querySelector(".workspace-header").getBoundingClientRect().bottom + 20),
    };
  });
  await page.touchscreen.tap(point.x, point.y);
  await page.waitForFunction(() => document.getElementById("ohlc").textContent.includes("暂无成交量"));
  assert.match(await page.locator("#ohlc").innerText(), /暂无成交量/);
  await page.locator("#chart-period").selectOption("week");
  assert.deepEqual(await series(), [200030]);
  assert.equal(await page.evaluate(() => volumeTest.chart.panes().length), 2);
  await page.evaluate(() => {
    const item = structuredClone(ChanceChart.current());
    item.asset.id = "test-without-volume";
    delete item.result.chart.volume_unit;
    ChanceChart.show(item);
  });
  assert.deepEqual(await series(), []);
  assert.match(await page.locator("#ohlc").innerText(), /暂无成交量/);
  await toggle.uncheck();
  assert.deepEqual(await series(), []);
  console.log("成交量日周切换、零值、缺量和触控读数通过");
} finally {
  await browser.close();
}
