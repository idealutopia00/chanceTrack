import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
  type IChartApi,
  type Time,
} from "lightweight-charts";
import { ChevronLeft, ChevronRight, Layers, X } from "lucide-react";
import {
  buildMarkerClusters,
  indexChartEvents,
  StrategyMarkersPrimitive,
  type IndexedChartEvent,
  type MarkerLayoutSummary,
  type MarkerThemePalette,
} from "../lib/chartMarkers";
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
  selectedEventIndex?: number;
  onSelectEventIndex?: (index: number) => void;
  onSelectEvent?: (ev: ChartEvent | null) => void;
}

const OVERLAY_PALETTE = [
  "#6aa1d8",
  "#d9a45b",
  "#9b84c7",
  "#4ea88a",
  "#d97a73",
];

const CHART_PALETTE: Record<"dark" | "light", MarkerThemePalette & { grid: string }> = {
  dark: {
    up: "#e26d65",
    down: "#3da67a",
    panel: "#15181e",
    text: "#8b93a2",
    muted: "#8b93a2",
    grid: "rgba(255,255,255,0.05)",
    border: "rgba(255,255,255,0.08)",
    warn: "#d9a45b",
    blue: "#6aa1d8",
    accent: "#3da67a",
    pillBg: "rgba(21, 24, 30, 0.94)",
    pillText: "#f2f4f8",
    focusRing: "#f2f4f8",
    solidRim: "rgba(255, 255, 255, 0.42)",
    stem: "rgba(236, 239, 244, 0.45)",
  },
  light: {
    up: "#c8463e",
    down: "#277d5b",
    panel: "#ffffff",
    text: "#67625a",
    muted: "#67625a",
    grid: "rgba(31,29,26,0.06)",
    border: "rgba(31,29,26,0.09)",
    warn: "#9a6319",
    blue: "#386b9c",
    accent: "#2d6a53",
    pillBg: "rgba(255, 255, 255, 0.96)",
    pillText: "#1f1d1a",
    focusRing: "#1f1d1a",
    solidRim: "rgba(31, 29, 26, 0.28)",
    stem: "rgba(31, 29, 26, 0.45)",
  },
};

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
  selectedEventIndex = -1,
  onSelectEventIndex,
  onSelectEvent,
}: CandleChartProps) {
  const mainContainerRef = useRef<HTMLDivElement | null>(null);
  const macdContainerRef = useRef<HTMLDivElement | null>(null);
  const mainChartRef = useRef<IChartApi | null>(null);
  const macdChartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ReturnType<IChartApi["addSeries"]> | null>(null);
  const markerPrimitiveRef = useRef<StrategyMarkersPrimitive | null>(null);
  const [hoverBar, setHoverBar] = useState<CandleBar | null>(null);
  const [compactLabels, setCompactLabels] = useState(false);
  const [selectedEventKey, setSelectedEventKey] = useState<string | null>(null);
  const [layoutCounts, setLayoutCounts] = useState<{ visible: number; folded: number }>({
    visible: 0,
    folded: 0,
  });

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

  const barSet = useMemo(() => new Set(computed.candles.map((c) => c.time)), [computed.candles]);
  const indexedEvents = useMemo(
    () => indexChartEvents(rawEvents, computed.dayToWeekTime, barSet),
    [rawEvents, computed.dayToWeekTime, barSet],
  );
  const markerClusters = useMemo(() => buildMarkerClusters(indexedEvents), [indexedEvents]);

  // Sync external stepper index -> selectedEventKey
  useEffect(() => {
    if (selectedEventIndex >= 0 && selectedEventIndex < indexedEvents.length) {
      setSelectedEventKey(indexedEvents[selectedEventIndex].eventKey);
    } else if (selectedEventIndex < 0) {
      // Clear selection when asset changes / resets
      setSelectedEventKey((prev) =>
        prev && indexedEvents.some((e) => e.eventKey === prev) ? prev : null,
      );
    }
  }, [selectedEventIndex, indexedEvents]);

  // Reset selected event when switching assets
  useEffect(() => {
    setSelectedEventKey(null);
  }, [data.asset.id]);

  const selectedEvent: IndexedChartEvent | null = useMemo(() => {
    if (!selectedEventKey) return null;
    return indexedEvents.find((e) => e.eventKey === selectedEventKey) ?? null;
  }, [selectedEventKey, indexedEvents]);

  const selectedClusterId = useMemo(() => {
    if (!selectedEvent) return null;
    const side = selectedEvent.resolvedDirection === "bullish" ? "below" : "above";
    return `${selectedEvent.mappedTime}:${side}`;
  }, [selectedEvent]);

  const sameDayEvents = useMemo(() => {
    if (!selectedEvent) return [];
    return indexedEvents.filter((e) => e.mappedTime === selectedEvent.mappedTime);
  }, [selectedEvent, indexedEvents]);

  const selectIndexedEvent = useCallback(
    (ev: IndexedChartEvent | null) => {
      setSelectedEventKey(ev?.eventKey ?? null);
      onSelectEvent?.(ev);
      if (ev && onSelectEventIndex) {
        const idx = indexedEvents.findIndex((item) => item.eventKey === ev.eventKey);
        if (idx >= 0) onSelectEventIndex(idx);
      } else if (!ev && onSelectEventIndex) {
        onSelectEventIndex(-1);
      }
    },
    [indexedEvents, onSelectEvent, onSelectEventIndex],
  );

  const stepSelectedEvent = useCallback(
    (delta: number) => {
      if (!indexedEvents.length) return;
      const cur = selectedEvent
        ? indexedEvents.findIndex((e) => e.eventKey === selectedEvent.eventKey)
        : -1;
      const nextIdx =
        cur < 0
          ? delta < 0
            ? indexedEvents.length - 1
            : 0
          : (cur + delta + indexedEvents.length) % indexedEvents.length;
      const next = indexedEvents[nextIdx];
      selectIndexedEvent(next);
      if (mainChartRef.current) {
        const barIdx = computed.candles.findIndex((c) => c.time >= next.mappedTime);
        if (barIdx >= 0) {
          const span = 45;
          mainChartRef.current.timeScale().setVisibleLogicalRange({
            from: Math.max(0, barIdx - span),
            to: Math.min(computed.candles.length + 3, barIdx + span),
          });
        }
      }
    },
    [indexedEvents, selectedEvent, selectIndexedEvent, computed.candles],
  );

  const hasMacd = computed.macd.length > 0;
  const displayBar = hoverBar ?? computed.candles[computed.candles.length - 1] ?? null;

  const handleLayoutChange = useCallback((summary: MarkerLayoutSummary) => {
    const el = mainContainerRef.current as (HTMLDivElement & { __ctMarkers?: MarkerLayoutSummary }) | null;
    if (el) el.__ctMarkers = summary;
    setLayoutCounts((prev) =>
      prev.visible === summary.visibleCount && prev.folded === summary.foldedCount
        ? prev
        : { visible: summary.visibleCount, folded: summary.foldedCount },
    );
  }, []);

  // Keep refs fresh for click listener without recreating chart
  const clustersRef = useRef(markerClusters);
  clustersRef.current = markerClusters;
  const selectEventRef = useRef(selectIndexedEvent);
  selectEventRef.current = selectIndexedEvent;
  const selectedKeyRef = useRef(selectedEventKey);
  selectedKeyRef.current = selectedEventKey;
  const showEventsRef = useRef(showEvents);
  showEventsRef.current = showEvents;

  // Build & populate charts when data / period / volume toggle / range changes
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
      rightPriceScale: {
        borderColor: borderCol,
        scaleMargins: { top: 0.16, bottom: showVolume ? 0.26 : 0.16 },
      },
      timeScale: { borderColor: borderCol, rightOffset: 5 },
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
        scaleMargins: { top: 0.82, bottom: 0 },
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

    // Attach custom high-contrast Strategy & Decision markers primitive on top layer
    const primitive = new StrategyMarkersPrimitive(pal, (summary) => {
      const host = mainContainerRef.current as (HTMLDivElement & { __ctMarkers?: MarkerLayoutSummary }) | null;
      if (host) host.__ctMarkers = summary;
      handleLayoutChange(summary);
    });
    candleSeries.attachPrimitive(primitive);
    markerPrimitiveRef.current = primitive;
    primitive.updateData({
      clusters: markerClusters,
      decisions,
      candles: computed.candles,
      dayToWeekTime: computed.dayToWeekTime,
      showEvents,
      showDecisions,
      compactLabels,
      selectedClusterId,
      selectedEventKey,
      palette: pal,
    });

    // Reliable direct click handler on main chart container (never swallowed after clicking external buttons)
    let downPt: { x: number; y: number } | null = null;
    const onPointerDown = (e: PointerEvent) => {
      downPt = { x: e.clientX, y: e.clientY };
    };
    const onMainClick = (e: MouseEvent) => {
      if (!showEventsRef.current) return;
      if (downPt && Math.hypot(e.clientX - downPt.x, e.clientY - downPt.y) > 6) {
        return;
      }
      const canvas = mainEl.querySelector("canvas");
      const rect = (canvas ?? mainEl).getBoundingClientRect();
      const rx = e.clientX - rect.left;
      const ry = e.clientY - rect.top;
      if (rx < 0 || ry < 0 || rx > rect.width || ry > rect.height) return;

      let targetCluster: (typeof clustersRef.current)[number] | undefined;
      const hit = primitive.hitTest(rx, ry);
      if (hit) {
        targetCluster = clustersRef.current.find((c) => c.id === hit.externalId);
      }
      if (!targetCluster) {
        const t = mainChart.timeScale().coordinateToTime(rx);
        if (t !== null) {
          const ts = String(t);
          targetCluster = clustersRef.current.find((c) => c.barTime === ts);
        }
      }
      if (targetCluster && targetCluster.events.length > 0) {
        const curIdx = targetCluster.events.findIndex((ev) => ev.eventKey === selectedKeyRef.current);
        const nextEv =
          curIdx >= 0
            ? targetCluster.events[(curIdx + 1) % targetCluster.events.length]
            : targetCluster.events[0];
        selectEventRef.current(nextEv);
      }
    };
    mainEl.addEventListener("pointerdown", onPointerDown);
    mainEl.addEventListener("click", onMainClick);

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
        rightPriceScale: { borderColor: borderCol },
        timeScale: { borderColor: borderCol, rightOffset: 5, visible: true },
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
        to: total + 4,
      });
    }

    return () => {
      mainEl.removeEventListener("pointerdown", onPointerDown);
      mainEl.removeEventListener("click", onMainClick);
      candleSeries.detachPrimitive(primitive);
      markerPrimitiveRef.current = null;
      mainChart.remove();
      mainChartRef.current = null;
      candleSeriesRef.current = null;
      if (macdChart) {
        macdChart.remove();
        macdChartRef.current = null;
      }
    };
  }, [computed, showVolume, hasMacd, period, range]);

  // Update marker primitive state WITHOUT rebuilding chart or losing zoom/pan!
  useEffect(() => {
    const primitive = markerPrimitiveRef.current;
    if (!primitive) return;
    primitive.updateData({
      clusters: markerClusters,
      decisions,
      candles: computed.candles,
      dayToWeekTime: computed.dayToWeekTime,
      showEvents,
      showDecisions,
      compactLabels,
      selectedClusterId,
      selectedEventKey,
      palette: CHART_PALETTE[resolvedTheme],
    });
  }, [
    markerClusters,
    decisions,
    computed,
    showEvents,
    showDecisions,
    compactLabels,
    selectedClusterId,
    selectedEventKey,
    resolvedTheme,
  ]);

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
    markerPrimitiveRef.current?.updatePalette(pal);
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
  const selectedDirColor = selectedEvent
    ? selectedEvent.resolvedDirection === "bullish"
      ? "var(--app-up)"
      : selectedEvent.resolvedDirection === "bearish"
        ? "var(--app-down)"
        : "var(--app-blue)"
    : "var(--app-muted)";
  const selectedDirLabel = selectedEvent
    ? selectedEvent.resolvedDirection === "bullish"
      ? "偏多"
      : selectedEvent.resolvedDirection === "bearish"
        ? "偏空"
        : "中性"
    : "";

  return (
    <div className="flex-1 min-h-0 flex flex-col gap-1.5">
      <div
        className="flex items-center justify-between gap-2 flex-wrap px-1 text-xs tabular"
        style={{ color: "var(--app-muted)" }}
      >
        {displayBar ? (
          <div className="flex items-center gap-2.5 flex-wrap font-mono text-[11px]">
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

        {showEvents && indexedEvents.length > 0 && (
          <div
            data-testid="chart-marker-legend"
            className="flex items-center gap-2 flex-wrap text-[10.5px]"
            style={{ color: "var(--app-muted)" }}
          >
            <span className="inline-flex items-center gap-1 whitespace-nowrap">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <polygon points="5,1 9,9 1,9" fill="var(--app-up)" />
              </svg>
              偏多
            </span>
            <span className="inline-flex items-center gap-1 whitespace-nowrap">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <polygon points="5,9 9,1 1,1" fill="var(--app-down)" />
              </svg>
              偏空
            </span>
            <span className="inline-flex items-center gap-1 whitespace-nowrap">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <polygon points="5,1 9,9 1,9" fill="currentColor" />
              </svg>
              实心已确认
            </span>
            <span className="inline-flex items-center gap-1 whitespace-nowrap">
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <polygon
                  points="5,1.5 8.5,8.5 1.5,8.5"
                  fill="var(--app-panel)"
                  stroke="currentColor"
                  strokeWidth="1.5"
                />
              </svg>
              空心盘中
            </span>
            <button
              type="button"
              data-testid="toggle-compact-labels"
              onClick={() => setCompactLabels((v) => !v)}
              className="px-1.5 py-0.5 rounded border text-[10.5px] inline-flex items-center gap-1 cursor-pointer whitespace-nowrap"
              style={{
                background: compactLabels ? "var(--app-elev)" : "transparent",
                borderColor: compactLabels ? "var(--app-border-strong)" : "var(--app-border)",
                color: compactLabels ? "var(--app-text)" : "var(--app-muted)",
              }}
              title="密集时可收拢标签，点击标记或使用前后按钮逐条查看"
            >
              <Layers className="w-3 h-3" />
              {compactLabels
                ? "展开标签"
                : layoutCounts.folded > 0
                  ? `收拢标签 (已收拢 ${layoutCounts.folded})`
                  : "收拢标签"}
            </button>
          </div>
        )}
      </div>

      {/* In-viewport selected event inspector (works even when labels are dense/folded) */}
      {showEvents && selectedEvent && (
        <div
          data-testid="chart-event-inspector"
          role="region"
          aria-label="选中事件详情"
          className="px-2.5 py-1.5 rounded-lg border flex items-center justify-between gap-2 flex-wrap text-xs"
          style={{
            background: "var(--app-elev)",
            borderColor: "var(--app-border-strong)",
          }}
        >
          <div className="flex items-center gap-2 flex-wrap min-w-0">
            <span
              className="inline-flex items-center gap-1 font-semibold shrink-0"
              style={{ color: selectedDirColor }}
            >
              <svg width="11" height="11" viewBox="0 0 10 10" aria-hidden="true">
                {selectedEvent.resolvedDirection === "bullish" ? (
                  <polygon
                    points="5,1 9,9 1,9"
                    fill={selectedEvent.provisional ? "var(--app-panel)" : "currentColor"}
                    stroke="currentColor"
                    strokeWidth={selectedEvent.provisional ? "1.6" : "0"}
                  />
                ) : selectedEvent.resolvedDirection === "bearish" ? (
                  <polygon
                    points="5,9 9,1 1,1"
                    fill={selectedEvent.provisional ? "var(--app-panel)" : "currentColor"}
                    stroke="currentColor"
                    strokeWidth={selectedEvent.provisional ? "1.6" : "0"}
                  />
                ) : (
                  <polygon
                    points="5,1 9,5 5,9 1,5"
                    fill={selectedEvent.provisional ? "var(--app-panel)" : "currentColor"}
                    stroke="currentColor"
                    strokeWidth={selectedEvent.provisional ? "1.6" : "0"}
                  />
                )}
              </svg>
              <span data-testid="inspector-event-name">{selectedEvent.event}</span>
            </span>

            {selectedEvent.name && (
              <span
                className="text-[11px] px-1.5 py-0.5 rounded"
                style={{ background: "var(--app-bg)", color: "var(--app-muted)" }}
              >
                {selectedEvent.name}
              </span>
            )}

            <span
              data-testid="inspector-event-date"
              className="font-mono tabular text-[11px]"
              style={{ color: "var(--app-text)" }}
            >
              {selectedEvent.date || selectedEvent.time}
            </span>

            <span
              className="text-[10.5px] px-1.5 py-0.5 rounded border"
              style={{
                borderColor: "var(--app-border-strong)",
                color: "var(--app-text)",
              }}
            >
              {selectedDirLabel}
            </span>

            <span
              data-testid="inspector-event-status"
              className="text-[10.5px] px-1.5 py-0.5 rounded font-medium"
              style={{
                background: selectedEvent.provisional ? "var(--app-panel)" : "var(--app-accent-soft)",
                color: selectedEvent.provisional ? "var(--app-text)" : "var(--app-accent)",
                border: selectedEvent.provisional
                  ? "1px dashed var(--app-border-strong)"
                  : "1px solid transparent",
              }}
            >
              {selectedEvent.provisional ? "盘中暂态 · 待收盘确认" : "收盘已确认"}
            </span>

            {selectedEvent.detail && (
              <span
                className="text-[11px] truncate max-w-[360px]"
                style={{ color: "var(--app-muted)" }}
                title={selectedEvent.detail}
              >
                {selectedEvent.detail}
              </span>
            )}
          </div>

          <div className="flex items-center gap-1 shrink-0">
            {sameDayEvents.length > 1 && (
              <div
                data-testid="inspector-same-day-switcher"
                className="inline-flex items-center gap-1 mr-1"
              >
                <span className="text-[10.5px]" style={{ color: "var(--app-muted)" }}>
                  同日 {sameDayEvents.length} 项:
                </span>
                {sameDayEvents.map((ev) => {
                  const active = ev.eventKey === selectedEvent.eventKey;
                  const dupName =
                    sameDayEvents.filter((item) => item.event === ev.event).length > 1;
                  return (
                    <button
                      key={ev.eventKey}
                      type="button"
                      onClick={() => selectIndexedEvent(ev)}
                      className="px-1.5 py-0.5 rounded border text-[10.5px] cursor-pointer"
                      style={{
                        background: active ? "var(--app-btn)" : "var(--app-bg)",
                        borderColor: active ? "var(--app-btn)" : "var(--app-border)",
                        color: active ? "var(--app-btn-text)" : "var(--app-text)",
                      }}
                    >
                      {dupName && ev.name ? `${ev.name} · ${ev.event}` : ev.event}
                    </button>
                  );
                })}
              </div>
            )}
            <button
              type="button"
              onClick={() => stepSelectedEvent(-1)}
              title="查看上一条事件"
              aria-label="查看上一条事件"
              className="p-1 rounded border cursor-pointer"
              style={{ borderColor: "var(--app-border)", color: "var(--app-muted)" }}
            >
              <ChevronLeft className="w-3 h-3" />
            </button>
            <button
              type="button"
              onClick={() => stepSelectedEvent(1)}
              title="查看下一条事件"
              aria-label="查看下一条事件"
              className="p-1 rounded border cursor-pointer"
              style={{ borderColor: "var(--app-border)", color: "var(--app-muted)" }}
            >
              <ChevronRight className="w-3 h-3" />
            </button>
            <button
              type="button"
              onClick={() => selectIndexedEvent(null)}
              title="关闭事件详情"
              aria-label="关闭事件详情"
              className="p-1 rounded border cursor-pointer"
              style={{ borderColor: "var(--app-border)", color: "var(--app-muted)" }}
            >
              <X className="w-3 h-3" />
            </button>
          </div>
        </div>
      )}

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
          data-visible-markers={layoutCounts.visible}
          data-folded-markers={layoutCounts.folded}
          data-label-mode={compactLabels ? "compact" : "auto"}
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
