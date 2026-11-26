import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  createSeriesMarkers,
  type IChartApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import { aggregateWeekly } from "../lib/weekly";
import type {
  AssetChartPayload,
  CandleBar,
  ChartEvent,
  DecisionRecord,
} from "../types";

interface CandleChartProps {
  data: AssetChartPayload;
  period: "day" | "week";
  range: "63" | "126" | "252" | "all";
  showVolume: boolean;
  showEvents: boolean;
  showDecisions: boolean;
  resolvedTheme: "dark" | "light";
  focusDate: string | null;
  onSelectEvent?: (ev: ChartEvent | null) => void;
}

const OVERLAY_PALETTE = [
  "#6aa1d8",
  "#d9a45b",
  "#9b84c7",
  "#4ea88a",
  "#d97a73",
];

const CHART_PALETTE = {
  dark: {
    up: "#e26d65",
    down: "#3da67a",
    panel: "#15181e",
    text: "#8b93a2",
    grid: "rgba(255,255,255,0.05)",
    border: "rgba(255,255,255,0.08)",
    warn: "#d9a45b",
    blue: "#6aa1d8",
    accent: "#3da67a",
  },
  light: {
    up: "#c8463e",
    down: "#277d5b",
    panel: "#ffffff",
    text: "#67625a",
    grid: "rgba(31,29,26,0.06)",
    border: "rgba(31,29,26,0.09)",
    warn: "#9a6319",
    blue: "#386b9c",
    accent: "#2d6a53",
  },
} as const;

const EMPTY_CANDLES: CandleBar[] = [];
const EMPTY_OVERLAYS: NonNullable<NonNullable<AssetChartPayload["result"]>["chart"]["overlays"]> = [];
const EMPTY_MACD: NonNullable<NonNullable<AssetChartPayload["result"]>["chart"]["macd"]> = [];
const EMPTY_EVENTS: ChartEvent[] = [];
const EMPTY_DECISIONS: DecisionRecord[] = [];

export function CandleChart({
  data,
  period,
  range,
  showVolume,
  showEvents,
  showDecisions,
  resolvedTheme,
  focusDate,
}: CandleChartProps) {
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const macdContainerRef = useRef<HTMLDivElement | null>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const macdChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ReturnType<IChartApi["addSeries"]> | null>(null);
  const [hoverBar, setHoverBar] = useState<CandleBar | null>(null);

  const chart = data.result?.chart;
  const rawCandles = chart?.candles ?? EMPTY_CANDLES;
  const rawOverlays = chart?.overlays ?? EMPTY_OVERLAYS;
  const rawMacd = chart?.macd ?? EMPTY_MACD;
  const rawEvents = chart?.strategy_events ?? EMPTY_EVENTS;
  const decisions = data.decisions ?? EMPTY_DECISIONS;

  const computed = useMemo(() => {
    if (period === "week") {
      return aggregateWeekly(rawCandles, rawOverlays, rawMacd);
    }
    const map = new Map<string, string>();
    for (const c of rawCandles) map.set(c.time, c.time);
    return {
      candles: rawCandles,
      overlays: rawOverlays,
      macd: rawMacd,
      dayToWeekTime: map,
    };
  }, [period, rawCandles, rawOverlays, rawMacd]);

  const hasMacd = computed.macd.length > 0;
  const displayBar = hoverBar ?? computed.candles[computed.candles.length - 1] ?? null;

  // Build & populate charts when data / period / toggles change (theme updates smoothly without destroying range!)
  useEffect(() => {
    const mainEl = mainContainerRef.current;
    if (!mainEl || !computed.candles.length) return;

    const pal = CHART_PALETTE[resolvedTheme];
    const upCol = pal.up;
    const downCol = pal.down;
    const panelBg = pal.panel;
    const textCol = pal.text;
    const gridCol = pal.grid;
    const borderCol = pal.border;
    const warnCol = pal.warn;
    const blueCol = pal.blue;
    const accentCol = pal.accent;

    const baseOpts = {
      layout: {
        background: { type: ColorType.Solid, color: panelBg },
        textColor: textCol,
        fontFamily:
          '-apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", sans-serif',
        fontSize: 11,
      },
      grid: {
        vertLines: { color: gridCol },
        horzLines: { color: gridCol },
      },
      crosshair: { mode: CrosshairMode.Normal },
      rightPriceScale: { borderColor: borderCol },
      timeScale: { borderColor: borderCol, rightOffset: 4 },
      autoSize: true,
    };

    const mainChart = createChart(mainEl, baseOpts);
    mainChartRef.current = mainChart;

    const candleSeries = mainChart.addSeries(CandlestickSeries, {
      upColor: upCol,
      downColor: downCol,
      borderUpColor: upCol,
      borderDownColor: downCol,
      wickUpColor: upCol,
      wickDownColor: downCol,
    });
    candleSeriesRef.current = candleSeries;
    candleSeries.setData(
      computed.candles.map((c) => ({
        time: c.time as Time,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      })),
    );

    // Overlays (Bollinger, MA, Donchian, Keltner, etc.)
    const groupColorMap = new Map<string, string>();
    computed.overlays.forEach((ov) => {
      const groupKey = ov.id.split(":")[0] || ov.id;
      if (!groupColorMap.has(groupKey)) {
        groupColorMap.set(
          groupKey,
          OVERLAY_PALETTE[groupColorMap.size % OVERLAY_PALETTE.length],
        );
      }
      const col = groupColorMap.get(groupKey)!;
      const isMiddle = ov.id.includes("中轨") || ov.name.includes("中轨");
      const line = mainChart.addSeries(LineSeries, {
        color: col,
        lineWidth: isMiddle ? 1 : 2,
        lineStyle: isMiddle ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
        crosshairMarkerVisible: false,
      });
      line.setData((ov.points || []).map((p) => ({ time: p.time as Time, value: p.value })));
    });

    // Volume on independent bottom scale
    if (showVolume && computed.candles.some((c) => typeof c.volume === "number")) {
      const volSeries = mainChart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        priceLineVisible: false,
        lastValueVisible: false,
      });
      mainChart.priceScale("volume").applyOptions({
        scaleMargins: { top: 0.8, bottom: 0 },
      });
      volSeries.setData(
        computed.candles
          .filter((c) => typeof c.volume === "number")
          .map((c) => ({
            time: c.time as Time,
            value: c.volume as number,
            color: c.close >= c.open ? `${upCol}44` : `${downCol}44`,
          })),
      );
    }

    // Build combined markers: Strategy Events + User Decisions on Candles
    const barSet = new Set(computed.candles.map((c) => c.time));
    const markers: SeriesMarker<Time>[] = [];

    if (showEvents) {
      for (const ev of rawEvents) {
        const evDate = ev.date || ev.time;
        if (!evDate) continue;
        const mapped = computed.dayToWeekTime.get(evDate) ?? evDate;
        if (!barSet.has(mapped)) continue;
        const dir =
          ev.direction ??
          (/金叉|向上突破|下轨外回归|短线回调|回调修复|释放 · 向上|相对转强/.test(ev.event)
            ? "bullish"
            : /死叉|向下突破|上轨外回归|退出|失效|释放 · 向下/.test(ev.event)
              ? "bearish"
              : "neutral");
        const isBull = dir === "bullish";
        const isBear = dir === "bearish";
        markers.push({
          time: mapped as Time,
          position: isBull ? "belowBar" : "aboveBar",
          color: ev.provisional
            ? warnCol
            : isBull
              ? upCol
              : isBear
                ? downCol
                : blueCol,
          shape: isBull ? "arrowUp" : isBear ? "arrowDown" : "circle",
          text: ev.event.slice(0, 6),
        });
      }
    }

    if (showDecisions) {
      const actionShort: Record<DecisionRecord["action"], string> = {
        买入: "买",
        卖出: "卖",
        持有: "持",
        观望: "观",
      };
      for (const d of decisions) {
        const mapped = computed.dayToWeekTime.get(d.bar_date) ?? d.bar_date;
        if (!barSet.has(mapped)) continue;
        const col =
          d.action === "买入"
            ? upCol
            : d.action === "卖出"
              ? downCol
              : d.action === "持有"
                ? accentCol
                : blueCol;
        markers.push({
          time: mapped as Time,
          position: "belowBar",
          color: col,
          shape: "circle",
          text: actionShort[d.action],
        });
      }
    }

    markers.sort((a, b) => String(a.time).localeCompare(String(b.time)));
    if (markers.length) {
      createSeriesMarkers(candleSeries, markers);
    }

    // Crosshair OHLC sync
    const barByTime = new Map(computed.candles.map((c) => [c.time, c]));
    mainChart.subscribeCrosshairMove((param) => {
      if (!param.time) {
        setHoverBar(null);
        return;
      }
      const found = barByTime.get(String(param.time));
      setHoverBar(found ?? null);
    });

    // MACD sub-pane
    let macdChart: IChartApi | null = null;
    if (hasMacd && macdContainerRef.current) {
      macdChart = createChart(macdContainerRef.current, {
        ...baseOpts,
        timeScale: { borderColor: borderCol, rightOffset: 4, visible: true },
      });
      macdChartRef.current = macdChart;

      const histSeries = macdChart.addSeries(HistogramSeries, {
        priceLineVisible: false,
        lastValueVisible: false,
      });
      histSeries.setData(
        computed.macd.map((p) => ({
          time: p.time as Time,
          value: p.histogram ?? 0,
          color: (p.histogram ?? 0) >= 0 ? `${upCol}88` : `${downCol}88`,
        })),
      );
      const difSeries = macdChart.addSeries(LineSeries, {
        color: warnCol,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      difSeries.setData(computed.macd.map((p) => ({ time: p.time as Time, value: p.dif })));
      const deaSeries = macdChart.addSeries(LineSeries, {
        color: blueCol,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      deaSeries.setData(computed.macd.map((p) => ({ time: p.time as Time, value: p.dea })));

      // Bidirectional time-scale sync
      let syncing = false;
      mainChart.timeScale().subscribeVisibleLogicalRangeChange((lr) => {
        if (syncing || !lr || !macdChart) return;
        syncing = true;
        macdChart.timeScale().setVisibleLogicalRange(lr);
        syncing = false;
      });
      macdChart.timeScale().subscribeVisibleLogicalRangeChange((lr) => {
        if (syncing || !lr) return;
        syncing = true;
        mainChart.timeScale().setVisibleLogicalRange(lr);
        syncing = false;
      });
    }

    // Apply initial visible range
    const total = computed.candles.length;
    if (range === "all") {
      mainChart.timeScale().fitContent();
    } else {
      const barsCount = period === "week" ? Math.ceil(Number(range) / 5) : Number(range);
      mainChart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, total - barsCount),
        to: total + 3,
      });
    }

    return () => {
      mainChart.remove();
      mainChartRef.current = null;
      candleSeriesRef.current = null;
      if (macdChart) {
        macdChart.remove();
        macdChartRef.current = null;
      }
    };
  }, [computed, showVolume, showEvents, showDecisions, rawEvents, decisions, hasMacd, period, range]);

  // Smooth theme re-styling WITHOUT resetting chart visible range!
  useEffect(() => {
    const pal = CHART_PALETTE[resolvedTheme];
    const themeOpts = {
      layout: {
        background: { type: ColorType.Solid, color: pal.panel },
        textColor: pal.text,
      },
      grid: { vertLines: { color: pal.grid }, horzLines: { color: pal.grid } },
      rightPriceScale: { borderColor: pal.border },
      timeScale: { borderColor: pal.border },
    };
    mainChartRef.current?.applyOptions(themeOpts);
    macdChartRef.current?.applyOptions(themeOpts);
    candleSeriesRef.current?.applyOptions({
      upColor: pal.up,
      downColor: pal.down,
      borderUpColor: pal.up,
      borderDownColor: pal.down,
      wickUpColor: pal.up,
      wickDownColor: pal.down,
    });
  }, [resolvedTheme]);

  // Jump viewport to focusDate when user clicks a signal or historical decision
  useEffect(() => {
    if (!focusDate || !mainChartRef.current || !computed.candles.length) return;
    const mapped = computed.dayToWeekTime.get(focusDate) ?? focusDate;
    const idx = computed.candles.findIndex((c) => c.time >= mapped);
    if (idx >= 0) {
      const span = 45;
      mainChartRef.current.timeScale().setVisibleLogicalRange({
        from: Math.max(0, idx - span),
        to: Math.min(computed.candles.length + 3, idx + span),
      });
    }
  }, [focusDate, computed]);

  if (!data.has_bars || !computed.candles.length) {
    return (
      <div
        className="flex-1 flex flex-col items-center justify-center rounded-lg border text-sm"
        style={{
          background: "var(--app-panel)",
          borderColor: "var(--app-border)",
          color: "var(--app-muted)",
        }}
      >
        <p className="mb-1 font-medium" style={{ color: "var(--app-text)" }}>
          暂无可用的真实日线数据
        </p>
        <p className="text-xs">点击右上角「立即检查」或单标的刷新获取行情。</p>
      </div>
    );
  }

  const lastMacd = computed.macd[computed.macd.length - 1];

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-1.5">
      <div
        className="flex items-center justify-between px-1 text-xs tabular"
        style={{ color: "var(--app-muted)" }}
      >
        {displayBar ? (
          <div className="flex items-center gap-3 flex-wrap font-mono text-[11px]">
            <span>{displayBar.time}</span>
            <span>开 {displayBar.open.toFixed(2)}</span>
            <span>高 {displayBar.high.toFixed(2)}</span>
            <span>低 {displayBar.low.toFixed(2)}</span>
            <span
              style={{
                color:
                  displayBar.close >= displayBar.open
                    ? "var(--app-up)"
                    : "var(--app-down)",
                fontWeight: 600,
              }}
            >
              收 {displayBar.close.toFixed(2)}
            </span>
            {typeof displayBar.volume === "number" && (
              <span>
                量 {displayBar.volume.toLocaleString("zh-CN", { maximumFractionDigits: 0 })}
                {chart?.volume_unit ? ` ${chart.volume_unit}` : ""}
              </span>
            )}
          </div>
        ) : (
          <span>移动十字线查看开高低收</span>
        )}
        <span className="text-[11px] hidden sm:inline">
          {period === "week" ? "周线视图 · 信号仍按日线" : "日线视图"}
        </span>
      </div>

      <div
        className="flex-1 min-h-0 flex flex-col rounded-lg border overflow-hidden"
        style={{
          background: "var(--app-panel)",
          borderColor: "var(--app-border)",
        }}
      >
        <div
          ref={mainContainerRef}
          className="w-full relative"
          style={{ flex: hasMacd ? "7 1 0%" : "1 1 0%", minHeight: 220 }}
          data-testid="main-chart"
        />
        {hasMacd && (
          <div
            className="w-full relative border-t"
            style={{
              flex: "3 1 0%",
              minHeight: 110,
              borderColor: "var(--app-border)",
            }}
          >
            <div
              className="absolute left-2 top-1 z-10 pointer-events-none flex items-center gap-2 text-[10.5px] font-mono tabular px-1.5 py-0.5 rounded"
              style={{ background: "var(--app-panel)", color: "var(--app-muted)" }}
            >
              <span>MACD</span>
              {lastMacd && (
                <>
                  <span style={{ color: "var(--app-warn)" }}>
                    DIF {lastMacd.dif.toFixed(3)}
                  </span>
                  <span style={{ color: "var(--app-blue)" }}>
                    DEA {lastMacd.dea.toFixed(3)}
                  </span>
                  <span
                    style={{
                      color: (lastMacd.histogram ?? 0) >= 0 ? "var(--app-up)" : "var(--app-down)",
                    }}
                  >
                    柱 {(lastMacd.histogram ?? 0) >= 0 ? "+" : ""}
                    {(lastMacd.histogram ?? 0).toFixed(3)}
                  </span>
                </>
              )}
            </div>
            <div ref={macdContainerRef} className="w-full h-full" data-testid="macd-chart" />
          </div>
        )}
      </div>
    </div>
  );
}
