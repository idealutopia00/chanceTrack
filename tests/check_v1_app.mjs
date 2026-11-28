// Playwright E2E check for Chance Track v1.0 React SPA on isolated synthetic SQLite data.
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || "playwright");
const base = process.env.CHANCE_TEST_URL || "http://127.0.0.1:8766";
const shotDir = process.env.V1_SHOT_DIR || "";

const browser = await chromium.launch({
  headless: true,
  executablePath:
    process.env.CHROME_BIN ||
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
});

const errors = [];
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
page.on("pageerror", (e) => errors.push(`pageerror: ${e.message}`));
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
});

async function saveShot(name) {
  if (!shotDir) return;
  fs.mkdirSync(shotDir, { recursive: true });
  await page.screenshot({ path: path.join(shotDir, name), fullPage: false });
}

async function assertChartMarkerGeometry(label) {
  await page.waitForFunction(() => Boolean(document.querySelector('[data-testid="main-chart"]')?.__ctMarkers));
  const report = await page.evaluate(() => {
    const host = document.querySelector('[data-testid="main-chart"]');
    const canvas = host?.querySelector("canvas");
    const layout = host?.__ctMarkers;
    const rect = canvas?.getBoundingClientRect();
    if (!layout || !rect) return { ok: false, reason: "missing __ctMarkers or canvas" };

    const overlap = (a, b) =>
      !(a.x + a.w <= b.x || b.x + b.w <= a.x || a.y + a.h <= b.y || b.y + b.h <= a.y);
    const arrowRect = (r) => ({
      x: r.x - 7.5,
      y: Math.min(r.tipY, r.baseY) - 2,
      w: 15,
      h: 14,
    });
    const foldedRect = (r) => ({
      x: r.x + 5.5,
      y: (r.tipY + r.baseY) / 2 - 7,
      w: 14,
      h: 14,
    });

    const rendered = layout.rendered ?? [];
    const decisions = layout.decisions ?? [];
    const violations = [];

    for (let i = 0; i < rendered.length; i++) {
      const a = rendered[i];
      if (!a.pillRect) continue;
      if (
        a.pillRect.x < -1 ||
        a.pillRect.y < -1 ||
        a.pillRect.x + a.pillRect.w > rect.width + 1 ||
        a.pillRect.y + a.pillRect.h > rect.height + 1
      ) {
        violations.push(`pill out of bounds: ${a.clusterId}`);
      }
      for (let j = i + 1; j < rendered.length; j++) {
        const b = rendered[j];
        if (b.pillRect && overlap(a.pillRect, b.pillRect)) {
          violations.push(`pill overlap: ${a.clusterId} vs ${b.clusterId}`);
        }
      }
      for (const other of rendered) {
        if (other.clusterId !== a.clusterId && overlap(a.pillRect, arrowRect(other))) {
          violations.push(`pill-arrow overlap: ${a.clusterId} vs ${other.clusterId}`);
        }
      }
    }

    const obstacles = [];
    for (const r of rendered) {
      obstacles.push({ id: `arrow:${r.clusterId}`, rect: arrowRect(r) });
      if (r.pillRect) obstacles.push({ id: `pill:${r.clusterId}`, rect: r.pillRect });
      else if (r.eventCount > 1) obstacles.push({ id: `folded:${r.clusterId}`, rect: foldedRect(r) });
    }
    for (let i = 0; i < decisions.length; i++) {
      const d = decisions[i];
      if (
        d.rect.x < -1 ||
        d.rect.y < -1 ||
        d.rect.x + d.rect.w > rect.width + 1 ||
        d.rect.y + d.rect.h > rect.height + 1
      ) {
        violations.push(`decision out of bounds: ${d.barTime}:${d.action}`);
      }
      for (const ob of obstacles) {
        if (overlap(d.rect, ob.rect)) {
          violations.push(`decision overlap: ${d.barTime}:${d.action} vs ${ob.id}`);
        }
      }
      for (let j = i + 1; j < decisions.length; j++) {
        if (overlap(d.rect, decisions[j].rect)) {
          violations.push(`decision-decision overlap: ${d.barTime} vs ${decisions[j].barTime}`);
        }
      }
    }

    return {
      ok: violations.length === 0,
      violations,
      visibleCount: layout.visibleCount,
      decisionCount: decisions.length,
    };
  });
  assert.equal(
    report.ok,
    true,
    `${label} chart marker geometry violations: ${JSON.stringify(report.violations ?? report.reason)}`,
  );
  return report;
}

try {
  // 1. Desktop Dark Mode: Workspace initial load
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(base, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="main-chart"] canvas');
  const overflowDesktop = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  assert.equal(overflowDesktop, 0, "Desktop workspace must have zero horizontal overflow");

  // 1b. Verify "有事件" filter, per-strategy summary rows, check date badge, refresh button, and count alignment
  const signalFilterBtn = page.locator('[data-watchlist-filter="signal"]');
  await signalFilterBtn.waitFor();
  assert.ok(
    (await signalFilterBtn.textContent())?.includes("有事件"),
    "Watchlist signal filter should be named 有事件",
  );
  const navCountEl = page.locator('[data-testid="nav-signal-record-count"]');
  const navCount = (await navCountEl.count()) ? Number((await navCountEl.textContent())?.trim()) : 0;
  await signalFilterBtn.click();
  const filteredCards = await page.locator("[data-asset-card]").count();
  assert.equal(filteredCards, navCount, "Nav badge count must match 有事件 filtered card count");
  if (filteredCards > 0) {
    const firstRow = page.locator("[data-strategy-summary-row]").first();
    await firstRow.waitFor();
    const rowText = (await firstRow.textContent()) ?? "";
    assert.ok(rowText.includes("MACD"), "Watchlist per-strategy row must show enabled strategy name");
    await page.locator("[data-asset-check-date]").first().waitFor();
  }
  await page.locator('[data-watchlist-filter="all"]').click();
  await page.locator('[data-testid="refresh-summaries-btn"]').click();
  await page.getByText(/已重算/).waitFor();
  await saveShot("01_desktop_dark_workspace.png");

  // 2. Switch asset & verify reload persistence
  await page.click('[data-asset-card="nasdaq100"]');
  await page.waitForFunction(() =>
    document.querySelector("h1")?.textContent?.includes("纳斯达克100"),
  );
  await page.reload({ waitUntil: "networkidle" });
  await page.waitForFunction(() =>
    document.querySelector("h1")?.textContent?.includes("纳斯达克100"),
  );

  // 3. Theme toggle preserves chart without crash
  await page.click('button[aria-label="切换主题"]');
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  await page.waitForTimeout(220);
  assert.ok(await page.locator('[data-testid="main-chart"] canvas').count());
  await saveShot("02_desktop_light_workspace.png");

  // Switch back to dark for remaining screenshots
  await page.click('button[aria-label="切换主题"]');
  await page.waitForFunction(() => document.documentElement.dataset.theme === "dark");

  // 4. Event stepper navigates strategy events cleanly & opens in-viewport inspector
  const prevEventBtn = page.locator('button[title="上一策略事件"]');
  if (await prevEventBtn.count()) {
    await prevEventBtn.click();
    await page.getByText(/^\d+\/\d+$/).waitFor();
    await page.locator('[data-testid="chart-event-inspector"]').waitFor();
    const legendVisible = await page.locator('[data-testid="chart-marker-legend"]').isVisible();
    assert.equal(legendVisible, true, "Chart marker legend must be visible when events exist");
  }

  // 5. Indicator exploration preview & restore
  await page.getByRole("button", { name: /指标探索/ }).click();
  await page.getByRole("button", { name: /\+ 布林突破 20\/2/ }).click();
  await page.getByText(/探索预览已更新/).waitFor();
  await page.getByRole("button", { name: "完成" }).click();
  await page.getByRole("button", { name: /还原正式/ }).click();
  await page.getByText(/已还原正式监测图表/).waitFor();

  // 6. Record decision without page reload
  await page.getByRole("button", { name: /^决策 \(/ }).click();
  await page.getByRole("button", { name: "买入", exact: true }).click();
  await page.locator("textarea").fill("Playwright v1 合成决策验证");
  await page.getByRole("button", { name: "保存本次判断" }).click();
  await page.getByText("Playwright v1 合成决策验证").waitFor();
  await page.waitForTimeout(120);
  const desktopGeom = await assertChartMarkerGeometry("desktop-1280x900");
  assert.ok(desktopGeom.decisionCount >= 1, "Recorded decision must render on main chart canvas");

  // 7. Pinned note modal opens cleanly without duplicate H1
  await page.getByRole("button", { name: /合成观察备忘/ }).click();
  await page.waitForSelector("#pinned-note-modal-title");
  const firstH1Visible = await page.evaluate(() => {
    const h1 = document.querySelector('[role="dialog"] .ct-prose > h1:first-child');
    return h1 ? getComputedStyle(h1).display !== "none" : false;
  });
  assert.equal(firstH1Visible, false, "Duplicate leading H1 inside pinned note modal must be hidden");
  await saveShot("03_desktop_dark_pinned_note.png");
  await page.keyboard.press("Escape");

  // 8. Catalog search finds 515450 by alias
  await page.click('button[aria-label="添加标的"]');
  await page.locator('[role="dialog"] input').first().fill("红利低波");
  await page.getByText("红利低波50ETF南方").waitFor();
  await page.keyboard.press("Escape");

  // 9. Signals view
  await page.getByRole("button", { name: /信号流/ }).click();
  await page.getByText("信号流与检查批次").waitFor();
  await page.getByText("加载中…").waitFor({ state: "detached" });
  const signalRows = await page.locator("tbody tr").count();
  assert.ok(signalRows >= 1, "Signals view should render populated signal events");
  await saveShot("04_desktop_dark_signals.png");

  // 10. Strategies library view + slide-over Escape close
  await page.getByRole("button", { name: /规则库/ }).click();
  await page.getByText("策略规则库").waitFor();
  await page.getByRole("button", { name: "编辑" }).first().waitFor();
  await saveShot("05_desktop_dark_strategies.png");
  await page.getByRole("button", { name: "编辑" }).first().click();
  await page.waitForSelector('[role="dialog"]');
  await page.keyboard.press("Escape");
  await page.locator('[role="dialog"]').waitFor({ state: "detached" });

  // 11. Notes view (light mode reading check + edit does not duplicate)
  await page.getByRole("button", { name: /备忘录/ }).click();
  await page.getByText(/投资备忘录 \(1\)/).waitFor();
  await page.getByRole("button", { name: "新建" }).click();
  await page.getByRole("button", { name: "取消" }).click();
  await page.getByRole("button", { name: "编辑" }).click();
  await page.getByText("编辑备忘录").waitFor();
  await page.getByRole("button", { name: "保存备忘" }).click();
  await page.getByText(/投资备忘录 \(1\)/).waitFor();
  await page.click('button[aria-label="切换主题"]');
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  await page.waitForTimeout(220);
  await saveShot("06_desktop_light_notes.png");

  // 12. Narrow viewport (390x844) check: NotesView & WorkspaceView zero horizontal overflow
  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(100);
  const overflowNotesMobile = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  assert.equal(overflowNotesMobile, 0, "Mobile notes view must have zero horizontal overflow");
  await page.getByRole("button", { name: /工作台/ }).click();
  await page.waitForSelector('[data-testid="main-chart"] canvas');
  await page.waitForTimeout(120);
  const mobileChecks = await page.evaluate(() => {
    const overflow = document.documentElement.scrollWidth - window.innerWidth;
    const header = document.querySelector("header");
    const buttons = Array.from(header?.querySelectorAll("button") ?? []).map((b) => {
      const r = b.getBoundingClientRect();
      return { left: r.left, right: r.right, height: r.height };
    });
    let overlap = false;
    for (let i = 1; i < buttons.length; i++) {
      if (buttons[i].left < buttons[i - 1].right - 1) overlap = true;
    }
    const tallButton = buttons.some((b) => b.height > 36);
    const activeRail = document.querySelector('nav button[aria-current="page"]')?.textContent?.trim() ?? "";
    return { overflow, overlap, tallButton, activeRail };
  });
  assert.equal(mobileChecks.overflow, 0, "Mobile workspace must have zero horizontal overflow");
  assert.equal(mobileChecks.overlap, false, "Mobile header buttons must not overlap");
  assert.equal(mobileChecks.tallButton, false, "Mobile header buttons must stay single-line");
  assert.equal(mobileChecks.activeRail, "工作台", "Workspace rail item must be active");
  const mobileGeom = await assertChartMarkerGeometry("mobile-390x844");
  assert.ok(mobileGeom.decisionCount >= 1, "Recorded decision must render on mobile chart canvas");
  await saveShot("07_mobile_light_workspace.png");

  assert.deepEqual(errors, [], `Unexpected browser errors: ${errors.join("; ")}`);
  console.log("PASS: Chance Track v1.0 E2E (desktop/mobile, dark/light, 4 views, charts, preview, decisions, notes, catalog, focus)");
} finally {
  await browser.close();
}
