// Run against the isolated workspace fixture in docs/development.md.
import assert from "node:assert/strict";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const browser = await chromium.launch({
  executablePath: process.env.CHROME_BIN ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});
const base = process.env.CHANCE_TEST_URL || "http://127.0.0.1:8766";
try {
  for (const width of [1440, 390]) {
    const page = await browser.newPage({
      viewport: { width, height: 1000 },
      hasTouch: width === 390,
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/static/app.js?*", async (route) => {
      const response = await route.fetch();
      const body = (await response.text()).replace(
        "chart.subscribeClick((event) => {",
        `window.testMarkers = { chart, priceMarks, macdMarks };
        chart.subscribeClick((event) => {`,
      );
      await route.fulfill({ response, body });
    });
    await page.goto(base);
    await page.waitForSelector('#chart[data-ready="true"]');
    const dates = await page.evaluate(() => {
      const item = structuredClone(ChanceChart.current());
      const chart = item.result.chart;
      const candles = chart.candles.slice(-14);
      const events = candles.slice(-3, -1);
      const bands = [20, 10].map((period) => ({
        kind: "boll_break", id: `boll-${period}`, name: `布林 ${period}`,
        params: { period, stddev: 2 },
      }));
      const macd = { kind: "macd", id: "macd-12", name: "MACD",
        params: { fast: 12, slow: 26, signal: 9 } };
      const points = candles.map(({ time }, index) => ({
        time, dif: index < 12 ? 0.3 : 0.2, dea: 0.25, histogram: index < 12 ? 0.05 : -0.05,
      }));
      chart.candles = candles;
      chart.macd = points;
      chart.macd_configs = [{ ...macd, points }];
      chart.overlays = bands.flatMap((band) =>
        [["上轨", 2], ["中轨", 0], ["下轨", -2]].map(([rail, delta]) => ({
          id: `${band.id}:${rail}`, kind: band.kind, name: `${band.name} · ${rail}`,
          points: candles.map(({ time, close }) => ({
            time, value: close + delta * (band.params.period === 10 ? 0.95 : 1),
          })),
        })),
      );
      item.result.strategies = [...bands, macd].map((config) => ({ ...config, config }));
      chart.strategy_events = [
        ...events.flatMap(({ time, close }, index) => bands.map((config) => ({
          time, price: close, name: config.name, config, event: "向下突破下轨",
          provisional: index === 1,
        }))),
        { time: events[1].time, name: "MACD", config: macd, event: "死叉" },
      ];
      ChanceChart.show(item, { view: { from: 0, to: candles.length + 2 } });
      return events.map(({ time }) => time);
    });
    await page.locator("#chart").scrollIntoViewIfNeeded();
    async function marker(pane, date, offset) {
      await page.evaluate(() => new Promise((resolve) =>
        requestAnimationFrame(() => requestAnimationFrame(resolve))));
      return page.evaluate(({ pane, date, offset }) => {
        const { chart, priceMarks, macdMarks } = testMarkers;
        const primitive = pane === 0 ? priceMarks : macdMarks;
        const x = chart.timeScale().timeToCoordinate(date);
        const element = [...document.querySelectorAll("#chart tr")].filter((row) => row.children.length === 3)[pane].children[1];
        const bounds = element.getBoundingClientRect();
        const ys = [];
        for (let y = 4; y < bounds.height - 4; y += 1) {
          if (primitive.hitTest(x, y)?.externalId === `event:${date}:${offset}`) ys.push(y);
        }
        return ys.length ? { x: bounds.x + x, y: bounds.y + ys[Math.floor(ys.length / 2)] } : null;
      }, { pane, date, offset });
    }
    await page.getByText("布林带（20，2）", { exact: true }).waitFor();
    await page.getByText("布林带（10，2）", { exact: true }).waitFor();
    assert.ok(await marker(0, dates[0], 0));
    assert.ok(await marker(0, dates[1], 0));
    assert.ok(await marker(0, dates[1], 1));
    assert.equal(await marker(0, dates[1], 2), null);
    const diamond = await marker(1, dates[1], 2);
    assert.ok(diamond);
    assert.equal(await marker(1, dates[1], 0), null);
    if (width === 390) await page.touchscreen.tap(diamond.x, diamond.y);
    else await page.mouse.click(diamond.x, diamond.y);
    const detail = page.locator("#chart-event-detail");
    assert.match(await detail.innerText(), /MACD（12\/26\/9）死叉.*DIF .*下穿 DEA/s);
    assert.equal(await page.locator('#chart-event-list [aria-current="true"]').getAttribute("data-event-offset"), "2");
    const sameDay = page.locator(`#chart-event-list button[data-event-date="${dates[1]}"][data-event-offset="1"]`);
    await sameDay.locator("..").evaluate((group) => group.open = true);
    await sameDay.click();
    assert.match(await detail.innerText(), /布林带（10，2）向下突破下轨/);
    const focus = page.getByRole("button", { name: "突出布林带（20，2）" });
    await focus.click();
    assert.equal(await focus.getAttribute("aria-pressed"), "true");
    assert.equal(await marker(0, dates[1], 1), null);
    await focus.click();
    assert.ok(await marker(0, dates[1], 1));
    await page.locator("#chart").scrollIntoViewIfNeeded();
    const triangle = await marker(0, dates[0], 0);
    assert.ok(triangle);
    if (width === 390) await page.touchscreen.tap(triangle.x, triangle.y);
    else await page.mouse.click(triangle.x, triangle.y);
    assert.match(await detail.innerText(), /布林带（20，2）向下突破下轨/);
    await page.getByRole("checkbox", { name: "MACD 副图" }).uncheck();
    assert.equal(await marker(1, dates[1], 2), null);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("桌面与窄屏指标事件检查通过");
} finally {
  await browser.close();
}
