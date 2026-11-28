// Deterministic canvas-geometry regression checks for frontend/src/lib/chartMarkers.ts.
// Guards against recurring marker/pill/arrow/decision overlap on desktop & narrow viewports,
// folded count badge obstacles, hidden-event click leaks, and weekly date disambiguation.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SRC = path.join(ROOT, "frontend", "src", "lib", "chartMarkers.ts");
const ESBUILD_BIN = path.join(
  ROOT,
  "frontend",
  "node_modules",
  ".bin",
  process.platform === "win32" ? "esbuild.cmd" : "esbuild",
);

const PALETTE = {
  up: "#e26d65",
  down: "#3da67a",
  panel: "#15181e",
  text: "#8b93a2",
  muted: "#8b93a2",
  border: "rgba(255,255,255,0.08)",
  warn: "#d9a45b",
  blue: "#6aa1d8",
  accent: "#3da67a",
  pillBg: "rgba(21, 24, 30, 0.94)",
  pillText: "#f2f4f8",
  focusRing: "#f2f4f8",
  solidRim: "rgba(255, 255, 255, 0.42)",
  stem: "rgba(236, 239, 244, 0.45)",
};

function rectsOverlapStrict(a, b) {
  return !(
    a.x + a.w <= b.x ||
    b.x + b.w <= a.x ||
    a.y + a.h <= b.y ||
    b.y + b.h <= a.y
  );
}

function arrowRectFor(r) {
  return {
    x: r.x - 7.5,
    y: Math.min(r.tipY, r.baseY) - 2,
    w: 15,
    h: 14,
  };
}

function foldedBadgeRectFor(r) {
  return {
    x: r.x + 5.5,
    y: (r.tipY + r.baseY) / 2 - 7,
    w: 14,
    h: 14,
  };
}

function assertInBounds(rect, width, height, desc) {
  assert.ok(rect.x >= -0.5, `${desc}: x=${rect.x} out of left bound`);
  assert.ok(rect.y >= -0.5, `${desc}: y=${rect.y} out of top bound`);
  assert.ok(rect.x + rect.w <= width + 0.5, `${desc}: right=${rect.x + rect.w} > ${width}`);
  assert.ok(rect.y + rect.h <= height + 0.5, `${desc}: bottom=${rect.y + rect.h} > ${height}`);
}

function assertLayoutNoOverlap(layout, width, height, label) {
  const rendered = layout.rendered ?? [];
  const decisions = layout.decisions ?? [];

  for (let i = 0; i < rendered.length; i++) {
    const a = rendered[i];
    if (a.pillRect) {
      assertInBounds(a.pillRect, width, height, `${label} pill ${a.clusterId}`);
      for (let j = i + 1; j < rendered.length; j++) {
        const b = rendered[j];
        if (b.pillRect) {
          assert.equal(
            rectsOverlapStrict(a.pillRect, b.pillRect),
            false,
            `${label}: pill ${a.clusterId} overlaps pill ${b.clusterId}`,
          );
        }
      }
      for (const other of rendered) {
        if (other.clusterId === a.clusterId) continue;
        assert.equal(
          rectsOverlapStrict(a.pillRect, arrowRectFor(other)),
          false,
          `${label}: pill ${a.clusterId} overlaps arrow ${other.clusterId}`,
        );
      }
    }
  }

  const obstacles = [];
  for (const r of rendered) {
    obstacles.push({ kind: `arrow(${r.clusterId})`, rect: arrowRectFor(r) });
    if (r.pillRect) {
      obstacles.push({ kind: `pill(${r.clusterId})`, rect: r.pillRect });
    } else if (r.eventCount > 1) {
      obstacles.push({ kind: `foldedCount(${r.clusterId})`, rect: foldedBadgeRectFor(r) });
    }
  }

  for (let i = 0; i < decisions.length; i++) {
    const d = decisions[i];
    assertInBounds(d.rect, width, height, `${label} decision ${d.barTime}:${d.action}`);
    for (const ob of obstacles) {
      assert.equal(
        rectsOverlapStrict(d.rect, ob.rect),
        false,
        `${label}: decision ${d.barTime}:${d.action} overlaps ${ob.kind}`,
      );
    }
    for (let j = i + 1; j < decisions.length; j++) {
      const other = decisions[j];
      assert.equal(
        rectsOverlapStrict(d.rect, other.rect),
        false,
        `${label}: decision ${d.barTime}:${d.action} overlaps decision ${other.barTime}:${other.action}`,
      );
    }
  }
}

function makeMockCtx() {
  const noop = () => {};
  return {
    font: "",
    textBaseline: "middle",
    textAlign: "left",
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 1,
    lineJoin: "round",
    save: noop,
    restore: noop,
    beginPath: noop,
    closePath: noop,
    moveTo: noop,
    lineTo: noop,
    arc: noop,
    arcTo: noop,
    stroke: noop,
    fill: noop,
    fillText: noop,
    setLineDash: noop,
    measureText(text) {
      let w = 0;
      for (const ch of String(text)) {
        w += ch.charCodeAt(0) > 127 ? 11 : 6.4;
      }
      return { width: w };
    },
  };
}

function makeCandles(count = 30) {
  const out = [];
  const start = new Date("2026-10-01T00:00:00Z");
  for (let i = 0; i < count; i++) {
    const d = new Date(start.getTime() + i * 86400_000);
    const time = d.toISOString().slice(0, 10);
    const base = 100 + Math.sin(i * 0.35) * 3;
    out.push({
      time,
      open: Number((base - 0.5).toFixed(2)),
      high: Number((base + 1.4).toFixed(2)),
      low: Number((base - 1.4).toFixed(2)),
      close: Number((base + 0.4).toFixed(2)),
      volume: 10000 + i * 100,
    });
  }
  return out;
}

function runPrimitiveDraw(primitive, candles, { width, height, barSpacing, rightPad = 40 }) {
  const indexByTime = new Map(candles.map((c, idx) => [c.time, idx]));
  const lastIdx = candles.length - 1;
  const minPrice = Math.min(...candles.map((c) => c.low));
  const maxPrice = Math.max(...candles.map((c) => c.high));
  const topMargin = height * 0.22;
  const bottomMargin = height * 0.28;
  const usableH = Math.max(40, height - topMargin - bottomMargin);

  const attached = {
    chart: {
      timeScale() {
        return {
          timeToCoordinate(t) {
            const idx = indexByTime.get(String(t));
            if (idx === undefined) return null;
            return width - rightPad - (lastIdx - idx) * barSpacing;
          },
        };
      },
    },
    series: {
      priceToCoordinate(price) {
        const ratio = (maxPrice - price) / Math.max(0.01, maxPrice - minPrice);
        return topMargin + ratio * usableH;
      },
    },
    requestUpdate() {},
  };

  primitive.attached(attached);
  const ctx = makeMockCtx();
  const target = {
    useMediaCoordinateSpace(cb) {
      cb({ context: ctx, mediaSize: { width, height } });
    },
  };
  primitive.paneViews()[0].renderer().draw(target);
  return primitive.getLastLayout();
}

const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "ct-chart-markers-"));
try {
  assert.ok(fs.existsSync(ESBUILD_BIN), `Missing esbuild binary at ${ESBUILD_BIN}`);
  const outFile = path.join(tmpDir, "chartMarkers.mjs");
  execFileSync(ESBUILD_BIN, [SRC, "--format=esm", "--platform=neutral", `--outfile=${outFile}`], {
    stdio: "pipe",
  });
  const mod = await import(pathToFileURL(outFile).href);
  const { StrategyMarkersPrimitive, indexChartEvents, buildMarkerClusters } = mod;

  const candles = makeCandles(30);
  const barSet = new Set(candles.map((c) => c.time));
  const dayMap = new Map(candles.map((c) => [c.time, c.time]));

  const denseEvents = [
    // Bar 20: same-bar multi-event bullish cluster + opposite-side bearish event
    { time: candles[20].time, date: candles[20].time, strategy_id: "macd", name: "MACD", event: "金叉", direction: "bullish" },
    { time: candles[20].time, date: candles[20].time, strategy_id: "boll", name: "布林突破", event: "下轨外回归", direction: "bullish" },
    { time: candles[20].time, date: candles[20].time, strategy_id: "donchian", name: "唐奇安", event: "向下突破下轨", direction: "bearish" },
    // Bar 21: immediately adjacent cross-side events forcing tier staggering
    { time: candles[21].time, date: candles[21].time, strategy_id: "macd", name: "MACD", event: "死叉", direction: "bearish" },
    { time: candles[21].time, date: candles[21].time, strategy_id: "rsi", name: "RSI", event: "短线回调", direction: "bullish" },
    // Bar 22: another adjacent bar with multi-event bearish cluster + provisional bullish event
    { time: candles[22].time, date: candles[22].time, strategy_id: "boll", name: "布林突破", event: "上轨外回归", direction: "bearish" },
    { time: candles[22].time, date: candles[22].time, strategy_id: "ma", name: "均线", event: "趋势失效", direction: "bearish" },
    { time: candles[22].time, date: candles[22].time, strategy_id: "macd", name: "MACD", event: "金叉", direction: "bullish", provisional: true },
  ];
  const indexed = indexChartEvents(denseEvents, dayMap, barSet);
  const clusters = buildMarkerClusters(indexed);
  assert.equal(clusters.length, 6, "Should cluster same-bar same-side events into 6 clusters");

  const decisions = [
    { id: 1, asset_id: "sp500", bar_date: candles[20].time, action: "买入", execution: "已提交", note: "", created_at: "" },
    { id: 2, asset_id: "sp500", bar_date: candles[21].time, action: "持有", execution: "计划中", note: "", created_at: "" },
    { id: 3, asset_id: "sp500", bar_date: candles[22].time, action: "卖出", execution: "已提交", note: "", created_at: "" },
    { id: 4, asset_id: "sp500", bar_date: candles[23].time, action: "观望", execution: "观察", note: "", created_at: "" },
  ];

  // 1. Desktop dense layout: cross-side pills/arrows, staggered tiers, and cross-bar decisions
  let latestFromCb = null;
  const primitive = new StrategyMarkersPrimitive(PALETTE, (summary) => {
    latestFromCb = summary;
  });
  primitive.updateData({
    clusters,
    decisions,
    candles,
    dayToWeekTime: dayMap,
    showEvents: true,
    showDecisions: true,
    compactLabels: false,
    selectedClusterId: clusters[0].id,
    selectedEventKey: clusters[0].events[0].eventKey,
    palette: PALETTE,
  });
  const desktopLayout = runPrimitiveDraw(primitive, candles, {
    width: 960,
    height: 420,
    barSpacing: 16,
    rightPad: 120,
  });
  assert.equal(desktopLayout.visibleCount, 6);
  assert.equal(latestFromCb.visibleCount, 6);
  assert.equal(desktopLayout.decisions.length, 4, "All 4 desktop decisions should be placed");
  assert.ok(
    desktopLayout.rendered.some((r) => r.tier > 0),
    "Adjacent dense markers should stagger onto higher tiers instead of colliding",
  );
  for (const r of desktopLayout.rendered) {
    assert.ok(r.clearancePx >= 10, `Marker ${r.clusterId} wick clearance should be >= 10px`);
  }
  assertLayoutNoOverlap(desktopLayout, 960, 420, "desktop-dense");

  // Hit testing works on both pill and arrow
  const withPill = desktopLayout.rendered.find((r) => r.pillRect);
  assert.ok(withPill, "Expected at least one marker with a visible pill");
  const pillHit = primitive.hitTest(
    withPill.pillRect.x + withPill.pillRect.w / 2,
    withPill.pillRect.y + withPill.pillRect.h / 2,
  );
  assert.equal(pillHit?.externalId, withPill.clusterId);
  const arrowHit = primitive.hitTest(withPill.x, (withPill.tipY + withPill.baseY) / 2);
  assert.equal(arrowHit?.externalId, withPill.clusterId);

  // 2. Narrow viewport (360x300, ~390px mobile chart pane): auto-fold & compactLabels folded count obstacles
  for (const compactLabels of [false, true]) {
    primitive.updateData({
      clusters,
      decisions,
      candles,
      dayToWeekTime: dayMap,
      showEvents: true,
      showDecisions: true,
      compactLabels,
      selectedClusterId: null,
      selectedEventKey: null,
      palette: PALETTE,
    });
    const narrowLayout = runPrimitiveDraw(primitive, candles, {
      width: 360,
      height: 300,
      barSpacing: 10,
      rightPad: 45,
    });
    assert.equal(narrowLayout.visibleCount, 6);
    assert.equal(
      narrowLayout.decisions.length,
      4,
      `Narrow viewport (compactLabels=${compactLabels}) should place all 4 decisions without dropping`,
    );
    assert.ok(
      narrowLayout.foldedCount > 0,
      `Narrow viewport (compactLabels=${compactLabels}) should fold colliding labels`,
    );
    if (compactLabels) {
      assert.ok(
        narrowLayout.rendered.some((r) => !r.pillRect && r.eventCount > 1),
        "Compact narrow layout must exercise folded multi-event count badge obstacle tracking",
      );
    }
    assertLayoutNoOverlap(narrowLayout, 360, 300, `narrow-compact-${compactLabels}`);
  }

  // 3. Hidden events (showEvents=false) must clear rendered markers and reject stale hitTest clicks
  primitive.updateData({
    clusters,
    decisions,
    candles,
    dayToWeekTime: dayMap,
    showEvents: false,
    showDecisions: true,
    compactLabels: false,
    selectedClusterId: withPill.clusterId,
    selectedEventKey: clusters[0].events[0].eventKey,
    palette: PALETTE,
  });
  const hiddenLayout = runPrimitiveDraw(primitive, candles, {
    width: 960,
    height: 420,
    barSpacing: 16,
    rightPad: 120,
  });
  assert.equal(hiddenLayout.visibleCount, 0);
  assert.equal(hiddenLayout.rendered.length, 0);
  assert.equal(
    primitive.hitTest(
      withPill.pillRect.x + withPill.pillRect.w / 2,
      withPill.pillRect.y + withPill.pillRect.h / 2,
    ),
    null,
    "Hidden events must not be clickable via pill coordinates",
  );
  assert.equal(
    primitive.hitTest(withPill.x, (withPill.tipY + withPill.baseY) / 2),
    null,
    "Hidden events must not be clickable via arrow coordinates",
  );
  assertLayoutNoOverlap(hiddenLayout, 960, 420, "hidden-events");

  // 4. Weekly aggregation mapping preserves distinct source dates for same-week disambiguation
  const weeklyMap = new Map([
    ["2026-11-16", "2026-11-20"],
    ["2026-11-18", "2026-11-20"],
  ]);
  const weeklyBarSet = new Set(["2026-11-20"]);
  const weeklyIndexed = indexChartEvents(
    [
      { time: "2026-11-16", date: "2026-11-16", strategy_id: "macd", name: "MACD", event: "金叉", direction: "bullish" },
      { time: "2026-11-18", date: "2026-11-18", strategy_id: "boll", name: "布林突破", event: "下轨外回归", direction: "bullish" },
    ],
    weeklyMap,
    weeklyBarSet,
  );
  const weeklyClusters = buildMarkerClusters(weeklyIndexed);
  assert.equal(weeklyClusters.length, 1);
  assert.deepEqual(
    weeklyClusters[0].events.map((e) => e.date),
    ["2026-11-16", "2026-11-18"],
    "Weekly marker cluster must retain original daily dates for switcher disambiguation",
  );
  assert.notEqual(weeklyClusters[0].events[0].eventKey, weeklyClusters[0].events[1].eventKey);

  console.log("PASS: chartMarkers canvas geometry regression (desktop, narrow 360px, folded badges, hidden events, weekly dates)");
} finally {
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
