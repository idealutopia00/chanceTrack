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
      viewport: { width, height: width === 390 ? 844 : 1000 },
      hasTouch: width === 390,
    });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.route("**/static/app.js?*", async (route) => {
      const response = await route.fetch();
      const body = (await response.text()).replace(
        "chart.subscribeClick((event) => {",
        `window.testMarkers = { chart, candles, dif, dea, priceMarks, macdMarks };
        chart.subscribeClick((event) => {`,
      ).replace("if (label) {", `
        (window.testLabels ??= {})[series === candles ? "price" : "macd"] =
          label && { date: label.point.date, offset: label.point.offset, name: label.point.name };
        if (label) {`).replace("const anchorX =", `
        (window.testLabelBoxes ??= {})[series === candles ? "price" : "macd"] = { left, top, width };
        const anchorX =`);
      await route.fulfill({ response, body });
    });
    await page.goto(base);
    await page.waitForSelector('#chart[data-ready="true"]');
    const dates = await page.evaluate(() => {
      const item = structuredClone(ChanceChart.current());
      const chart = item.result.chart;
      const candles = chart.candles.slice(-14).map(({ time }, index) => {
        const close = 109 + index / 10;
        return { time, open: close - 0.2, close, high: close + 0.3, low: close - 0.3 };
      });
      const events = candles.slice(-3, -1);
      Object.assign(events[0], { open: 107, close: 85, high: 107, low: 85 });
      const bands = [20, 10].map((period) => ({
        kind: "boll_break", id: `boll-${period}`, name: `布林 ${period}`,
        params: { period, stddev: 2 },
      }));
      const macd = { kind: "macd", id: "macd-12", name: "MACD",
        params: { fast: 12, slow: 26, signal: 9 } };
      const points = candles.map(({ time }, index) => ({
        time, dif: index < 12 ? 0.3 : 0.2, dea: 0.25, histogram: index < 12 ? 0.05 : -0.05,
      }));
      const secondMacd = { ...macd, id: "macd-8", params: { fast: 8, slow: 17, signal: 9 } };
      chart.candles = candles;
      chart.macd = points;
      chart.macd_configs = [{ ...macd, points }, { ...secondMacd, points }];
      chart.overlays = bands.flatMap((band) =>
        [["上轨", 2], ["中轨", 0], ["下轨", -2]].map(([rail, delta]) => ({
          id: `${band.id}:${rail}`, kind: band.kind, name: `${band.name} · ${rail}`,
          points: candles.map(({ time, close }) => ({
            time, value: time === events[0].time && band.params.period === 20
              ? 109.0002 + delta : close + delta * (band.params.period === 10 ? 0.95 : 1),
          })),
        })),
      );
      item.result.strategies = [...bands, macd, secondMacd].map((config) => ({ ...config, config }));
      chart.strategy_events = [
        ...events.flatMap(({ time, close }, index) => bands.map((config) => ({
          time, price: close, name: config.name, config, event: "向下突破下轨",
          provisional: index === 1,
        }))),
        { time: events[1].time, name: "MACD", config: macd, event: "死叉" },
        { time: events[1].time, name: "MACD", config: secondMacd, event: "死叉" },
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
    const label = (pane) => page.evaluate((key) => testLabels[key] ?? null, pane);
    await page.waitForFunction(() => window.testLabels?.price && window.testLabels?.macd);
    assert.deepEqual(await label("price"), { date: dates[1], offset: 0, name: "跌破下轨" });
    assert.deepEqual(await label("macd"), { date: dates[1], offset: 2, name: "死叉" });
    await page.evaluate(() => testMarkers.chart.timeScale().setVisibleLogicalRange({ from: 6, to: 13 }));
    await page.evaluate(() => new Promise((resolve) =>
      requestAnimationFrame(() => requestAnimationFrame(resolve))));
    const coversCurve = await page.evaluate(() => {
      const { left, top, width } = testLabelBoxes.macd;
      const { chart, dif, dea } = testMarkers;
      return [dif, dea].some((line) => line.data().slice(1).some((p, i) => {
        const q = line.data()[i];
        const x1 = chart.timeScale().timeToCoordinate(q.time);
        const x2 = chart.timeScale().timeToCoordinate(p.time);
        const y1 = line.priceToCoordinate(q.value);
        const y2 = line.priceToCoordinate(p.value);
        return left <= x2 && left + width >= x1 &&
          top <= Math.max(y1, y2) && top + 20 >= Math.min(y1, y2);
      }));
    });
    assert.equal(coversCurve, false);
    await page.evaluate(() => testMarkers.chart.timeScale().setVisibleLogicalRange({ from: 0, to: 10 }));
    await page.waitForFunction(() => !testLabels.price && !testLabels.macd);
    await page.evaluate(() => testMarkers.chart.timeScale().setVisibleLogicalRange({ from: 0, to: 16 }));
    await page.waitForFunction(() => testLabels.price && testLabels.macd);
    const gapMark = await marker(0, dates[0], 0);
    assert.ok(gapMark);
    const gapBodyBottom = await page.evaluate((date) => {
      const bar = ChanceChart.current().result.chart.candles.find((bar) => bar.time === date);
      const pane = document.querySelector("#chart tr").children[1];
      return pane.getBoundingClientRect().top + testMarkers.candles.priceToCoordinate(bar.close);
    }, dates[0]);
    assert.ok(gapMark.y - 4 > gapBodyBottom);
    assert.ok(await marker(0, dates[1], 0));
    assert.ok(await marker(0, dates[1], 1));
    assert.equal(await marker(0, dates[1], 2), null);
    const diamond = await marker(1, dates[1], 2);
    assert.ok(diamond);
    assert.equal(await marker(1, dates[1], 0), null);
    const view = () => page.evaluate(() => ({
      scroll: window.scrollY, range: testMarkers.chart.timeScale().getVisibleLogicalRange(),
    }));
    const before = await view();
    if (width === 390) await page.touchscreen.tap(diamond.x, diamond.y);
    else await page.mouse.click(diamond.x, diamond.y);
    const detail = page.locator("#chart-event-detail");
    assert.deepEqual(await view(), before);
    const summary = page.locator("#chart-event-summary");
    assert.match(await summary.innerText(), /死叉.*DIF .*下穿 DEA/s);
    assert.equal(await detail.locator("details").evaluate((node) => node.open), false);
    assert.equal(await marker(1, dates[1], 3), null);
    assert.equal(await page.locator('#chart-event-list [aria-current="true"]').getAttribute("data-event-offset"), "2");
    const otherMacd = page.locator(`#chart-event-list button[data-event-date="${dates[1]}"][data-event-offset="3"]`);
    await otherMacd.click();
    assert.match(await detail.textContent(), /MACD（8\/17\/9）死叉/);
    assert.equal(await page.getByLabel("MACD 图表配置").inputValue(), "1");
    assert.ok(await marker(1, dates[1], 3));
    assert.equal(await marker(1, dates[1], 2), null);
    const sameDay = page.locator(`#chart-event-list button[data-event-date="${dates[1]}"][data-event-offset="1"]`);
    await sameDay.locator("..").evaluate((group) => group.open = true);
    await sameDay.click();
    await page.waitForFunction(() => testLabels.price?.offset === 1 && !testLabels.macd);
    assert.match(await detail.textContent(), /布林带（10，2）向下突破下轨/);
    const focus = page.getByRole("button", { name: "突出布林带（20，2）" });
    await focus.click();
    assert.equal(await focus.getAttribute("aria-pressed"), "true");
    assert.equal(await marker(0, dates[1], 1), null);
    await focus.click();
    assert.ok(await marker(0, dates[1], 1));
    await page.locator("#chart").scrollIntoViewIfNeeded();
    await page.locator("#chart").evaluate((node) =>
      window.scrollBy(0, node.getBoundingClientRect().top + 100));
    const partiallyScrolled = await view();
    const triangle = await marker(0, dates[0], 0);
    assert.ok(triangle);
    if (width === 390) await page.touchscreen.tap(triangle.x, triangle.y);
    else await page.mouse.click(triangle.x, triangle.y);
    assert.match(await detail.textContent(), /布林带（20，2）向下突破下轨/);
    assert.match(await summary.innerText(), /下轨.*收盘价 85/);
    assert.deepEqual(await view(), partiallyScrolled);
    const partialSummary = await summary.boundingBox();
    const header = await page.locator(".workspace-header").boundingBox();
    assert.ok(partialSummary.y >= header.y + header.height);
    assert.ok(partialSummary.y + partialSummary.height < page.viewportSize().height);
    assert.ok(await summary.evaluate((node) => {
      const { x, y, width, height } = node.getBoundingClientRect();
      node.style.pointerEvents = "auto";
      const top = document.elementFromPoint(x + width / 2, y + height / 2);
      node.style.removeProperty("pointer-events");
      return node.contains(top);
    }));
    const previous = page.getByRole("button", { name: "上一事件" });
    const next = page.getByRole("button", { name: "下一事件" });
    await next.scrollIntoViewIfNeeded();
    const navigationView = await view();
    await next.click();
    await previous.click();
    assert.deepEqual(await view(), navigationView);
    await next.click();
    await page.evaluate(() => testMarkers.chart.timeScale().setVisibleLogicalRange({ from: 12, to: 16 }));
    const narrowView = await view();
    await previous.click();
    await page.waitForFunction((date) => {
      const x = testMarkers.chart.timeScale().timeToCoordinate(date);
      return x > 0 && x < testMarkers.chart.timeScale().width();
    }, dates[0]);
    const shiftedView = await view();
    assert.equal(shiftedView.scroll, narrowView.scroll);
    assert.equal(shiftedView.range.to - shiftedView.range.from, 4);
    assert.notDeepEqual(shiftedView.range, narrowView.range);
    const box = await summary.boundingBox();
    assert.ok(box.y >= 0 && box.y + box.height < page.viewportSize().height);
    await page.keyboard.press("Escape");
    assert.equal(await summary.isHidden(), true);
    await page.getByRole("button", { name: "回到最新" }).click();
    await page.waitForFunction(() => testLabels.price?.offset === 0);
    await page.getByRole("checkbox", { name: "显示策略事件" }).uncheck();
    await page.waitForFunction(() => !testLabels.price && !testLabels.macd);
    await page.getByRole("checkbox", { name: "显示策略事件" }).check();
    await page.getByRole("checkbox", { name: "MACD 副图" }).uncheck();
    assert.equal(await marker(1, dates[1], 3), null);
    assert.deepEqual(errors, []);
    await page.close();
  }
  console.log("桌面与窄屏指标事件检查通过");
} finally {
  await browser.close();
}
