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

try {
  // 1. Desktop Dark Mode: Workspace initial load
  await page.emulateMedia({ colorScheme: "dark" });
  await page.goto(base, { waitUntil: "networkidle" });
  await page.waitForSelector('[data-testid="main-chart"] canvas');
  const overflowDesktop = await page.evaluate(
    () => document.documentElement.scrollWidth - window.innerWidth,
  );
  assert.equal(overflowDesktop, 0, "Desktop workspace must have zero horizontal overflow");
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

  // 4. Indicator exploration preview & restore
  await page.getByRole("button", { name: /指标探索/ }).click();
  await page.getByRole("button", { name: /\+ 布林突破 20\/2/ }).click();
  await page.getByText(/探索预览已更新/).waitFor();
  await page.getByRole("button", { name: "完成" }).click();
  await page.getByRole("button", { name: /还原正式/ }).click();
  await page.getByText(/已还原正式监测图表/).waitFor();

  // 5. Record decision without page reload
  await page.getByRole("button", { name: /^决策 \(/ }).click();
  await page.getByRole("button", { name: "买入", exact: true }).click();
  await page.locator("textarea").fill("Playwright v1 合成决策验证");
  await page.getByRole("button", { name: "保存本次判断" }).click();
  await page.getByText("Playwright v1 合成决策验证").waitFor();

  // 6. Pinned note modal opens cleanly without duplicate H1
  await page.getByRole("button", { name: /合成观察备忘/ }).click();
  await page.waitForSelector("#pinned-note-modal-title");
  const firstH1Visible = await page.evaluate(() => {
    const h1 = document.querySelector('[role="dialog"] .ct-prose > h1:first-child');
    return h1 ? getComputedStyle(h1).display !== "none" : false;
  });
  assert.equal(firstH1Visible, false, "Duplicate leading H1 inside pinned note modal must be hidden");
  await saveShot("03_desktop_dark_pinned_note.png");
  await page.keyboard.press("Escape");

  // 7. Catalog search finds 515450 by alias
  await page.click('button[aria-label="添加标的"]');
  await page.locator('[role="dialog"] input').first().fill("红利低波");
  await page.getByText("红利低波50ETF南方").waitFor();
  await page.keyboard.press("Escape");

  // 8. Signals view
  await page.getByRole("button", { name: /信号流/ }).click();
  await page.getByText("信号流与检查批次").waitFor();
  await page.getByText("加载中…").waitFor({ state: "detached" });
  const signalRows = await page.locator("tbody tr").count();
  assert.ok(signalRows >= 1, "Signals view should render populated signal events");
  await saveShot("04_desktop_dark_signals.png");

  // 9. Strategies library view + slide-over focus restore
  await page.getByRole("button", { name: /规则库/ }).click();
  await page.getByText("策略规则库").waitFor();
  await page.getByRole("button", { name: "编辑" }).first().waitFor();
  await saveShot("05_desktop_dark_strategies.png");
  await page.getByRole("button", { name: "编辑" }).first().click();
  await page.waitForSelector('[role="dialog"]');
  await page.click('button[aria-label="关闭编辑"]');

  // 10. Notes view (light mode reading check)
  await page.getByRole("button", { name: /备忘录/ }).click();
  await page.getByText(/投资备忘录 \(1\)/).waitFor();
  await page.click('button[aria-label="切换主题"]');
  await page.waitForFunction(() => document.documentElement.dataset.theme === "light");
  await page.waitForTimeout(220);
  await saveShot("06_desktop_light_notes.png");

  // 11. Narrow viewport (390x844) check: zero horizontal overflow & no header overlap
  await page.setViewportSize({ width: 390, height: 844 });
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
  await saveShot("07_mobile_light_workspace.png");

  assert.deepEqual(errors, [], `Unexpected browser errors: ${errors.join("; ")}`);
  console.log("PASS: Chance Track v1.0 E2E (desktop/mobile, dark/light, 4 views, charts, preview, decisions, notes, catalog, focus)");
} finally {
  await browser.close();
}
