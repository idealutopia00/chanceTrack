import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  BookOpen,
  ChevronLeft,
  ChevronRight,
  Layers,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Star,
} from "lucide-react";
import { api } from "../api";
import { CandleChart } from "../components/CandleChart";
import { sortEventsChronologically } from "../lib/chartMarkers";
import type {
  AssetChartPayload,
  AssetSummary,
  ChartTemplate,
  DecisionRecord,
  StrategyRule,
} from "../types";

interface WorkspaceViewProps {
  version: number;
  assets: AssetSummary[];
  strategies: StrategyRule[];
  templates: ChartTemplate[];
  selectedAssetId: string | null;
  focusDate: string | null;
  watchlistOpen: boolean;
  drawerOpen: boolean;
  resolvedTheme: "dark" | "light";
  onSelectAsset: (assetId: string) => void;
  onFocusDateChange: (date: string | null) => void;
  onOpenAddAsset: () => void;
  onOpenNote: (noteId?: number) => void;
  onRefreshBootstrap: () => Promise<void>;
}

const EXPLORATION_PRESETS: StrategyRule[] = [
  { id: "exp_macd", name: "MACD 12/26/9", kind: "macd", params: { fast: 12, slow: 26, signal: 9 } },
  { id: "exp_boll", name: "布林突破 20/2", kind: "boll_break", params: { period: 20, stddev: 2 } },
  { id: "exp_ma", name: "双均线 20/60", kind: "ma", params: { fast: 20, slow: 60 } },
  { id: "exp_donchian", name: "唐奇安 20/10", kind: "donchian", params: { entry: 20, exit: 10 } },
];

export function WorkspaceView({
  version,
  assets,
  strategies,
  templates,
  selectedAssetId,
  focusDate,
  watchlistOpen,
  drawerOpen,
  resolvedTheme,
  onSelectAsset,
  onFocusDateChange,
  onOpenAddAsset,
  onOpenNote,
  onRefreshBootstrap,
}: WorkspaceViewProps) {
  const [wlSearch, setWlSearch] = useState("");
  const [wlFilter, setWlFilter] = useState<"all" | "signal" | "star" | "pending">("all");
  const [chartData, setChartData] = useState<AssetChartPayload | null>(null);
  const [loadingChart, setLoadingChart] = useState(false);
  const [chartError, setChartError] = useState<string | null>(null);

  const [period, setPeriod] = useState<"day" | "week">("day");
  const [range, setRange] = useState<"63" | "126" | "252" | "all">("126");
  const [showVolume, setShowVolume] = useState(true);
  const [showEvents, setShowEvents] = useState(true);
  const [showDecisions, setShowDecisions] = useState(true);
  const [eventCursor, setEventCursor] = useState<number>(-1);

  // Exploration state (zero formal side effects)
  const [showIndicatorMenu, setShowIndicatorMenu] = useState(false);
  const [explorationRules, setExplorationRules] = useState<StrategyRule[] | null>(null);
  const [templateName, setTemplateName] = useState("");
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  // Right drawer tabs & decision form
  const [drawerTab, setDrawerTab] = useState<"signals" | "decisions" | "notes">("signals");
  const [decAction, setDecAction] = useState<DecisionRecord["action"]>("观望");
  const [decExec, setDecExec] = useState<DecisionRecord["execution"]>("未执行");
  const [decNote, setDecNote] = useState("");
  const [attachExploration, setAttachExploration] = useState(false);

  const activeAssets = useMemo(
    () => assets.filter((a) => !a.archived && a.watchlisted),
    [assets],
  );

  const currentAssetId = selectedAssetId ?? activeAssets[0]?.id ?? null;
  const activeAssetRef = useRef<string | null>(currentAssetId);
  activeAssetRef.current = currentAssetId;
  const requestSeqRef = useRef(0);

  // Close indicator exploration popover on Escape
  useEffect(() => {
    if (!showIndicatorMenu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setShowIndicatorMenu(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [showIndicatorMenu]);

  // Load formal chart bars when asset changes
  useEffect(() => {
    if (!currentAssetId) {
      setChartData(null);
      return;
    }
    const seq = ++requestSeqRef.current;
    let cancelled = false;
    setLoadingChart(true);
    setChartError(null);
    setExplorationRules(null);
    setEventCursor(-1);
    api
      .assetBars(currentAssetId)
      .then((res) => {
        if (!cancelled && seq === requestSeqRef.current) setChartData(res);
      })
      .catch((e) => {
        if (!cancelled && seq === requestSeqRef.current) {
          setChartError(e instanceof Error ? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled && seq === requestSeqRef.current) setLoadingChart(false);
      });
    return () => {
      cancelled = true;
    };
  }, [currentAssetId]);

  const filteredGroupedAssets = useMemo(() => {
    const q = wlSearch.trim().toLowerCase();
    const groups = new Map<string, AssetSummary[]>();
    for (const a of activeAssets) {
      if (wlFilter === "star" && !a.priority) continue;
      if (wlFilter === "signal" && !a.latest_signal) continue;
      if (wlFilter === "pending" && !a.is_provisional) continue;
      if (q) {
        const hay = `${a.label} ${a.code} ${a.ticker} ${(a.aliases || []).join(" ")}`.toLowerCase();
        if (!hay.includes(q)) continue;
      }
      const g = a.group_name || a.group || "未分组";
      const list = groups.get(g) ?? [];
      list.push(a);
      groups.set(g, list);
    }
    return Array.from(groups.entries());
  }, [activeAssets, wlSearch, wlFilter]);

  const strategyEvents = useMemo(
    () => sortEventsChronologically(chartData?.result?.chart?.strategy_events ?? []),
    [chartData?.result?.chart?.strategy_events],
  );

  function jumpEvent(delta: number) {
    if (!strategyEvents.length) return;
    const nextIdx =
      eventCursor < 0
        ? delta < 0
          ? strategyEvents.length - 1
          : 0
        : Math.max(0, Math.min(strategyEvents.length - 1, eventCursor + delta));
    setEventCursor(nextIdx);
    const target = strategyEvents[nextIdx];
    const targetDate = target.date || target.time || null;
    if (targetDate) onFocusDateChange(targetDate);
  }

  async function runExplorationPreview(nextRules: StrategyRule[]) {
    if (!currentAssetId) return;
    const reqAssetId = currentAssetId;
    const seq = ++requestSeqRef.current;
    setBusyAction("preview");
    setStatusMessage(null);
    try {
      const res = await api.previewAsset(reqAssetId, nextRules);
      if (activeAssetRef.current !== reqAssetId || seq !== requestSeqRef.current) return;
      setExplorationRules(nextRules);
      setChartData(res);
      setStatusMessage("探索预览已更新 · 无正式通知与数据库副作用");
    } catch (e) {
      if (activeAssetRef.current === reqAssetId) {
        setStatusMessage(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (seq === requestSeqRef.current) setBusyAction(null);
    }
  }

  async function restoreFormalChart() {
    if (!currentAssetId) return;
    const reqAssetId = currentAssetId;
    const seq = ++requestSeqRef.current;
    setBusyAction("restore");
    try {
      const res = await api.assetBars(reqAssetId);
      if (activeAssetRef.current !== reqAssetId || seq !== requestSeqRef.current) return;
      setExplorationRules(null);
      setChartData(res);
      setStatusMessage("已还原正式监测图表");
    } catch (e) {
      if (activeAssetRef.current === reqAssetId) {
        setStatusMessage(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (seq === requestSeqRef.current) setBusyAction(null);
    }
  }

  async function handleToggleStar(asset: AssetSummary, e: React.MouseEvent) {
    e.stopPropagation();
    try {
      await api.updateAsset(asset.id, { version, priority: !asset.priority });
      await onRefreshBootstrap();
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : String(err));
    }
  }

  async function handleToggleMonitor(strategyId: string, bound: boolean) {
    if (!currentAssetId) return;
    const reqAssetId = currentAssetId;
    const seq = ++requestSeqRef.current;
    setBusyAction(`mon:${strategyId}`);
    setStatusMessage(null);
    try {
      await api.updateMonitoring(reqAssetId, {
        version,
        action: bound ? "unbind" : "bind",
        strategy_id: strategyId,
      });
      await onRefreshBootstrap();
      const refreshed = await api.assetBars(reqAssetId);
      if (activeAssetRef.current !== reqAssetId || seq !== requestSeqRef.current) return;
      setChartData(refreshed);
      setExplorationRules(null);
      setStatusMessage(bound ? "已取消该标的自动监测" : "已加入该标的自动监测（下次检查生效）");
    } catch (e) {
      if (activeAssetRef.current === reqAssetId) {
        setStatusMessage(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (seq === requestSeqRef.current) setBusyAction(null);
    }
  }

  async function handleRefreshSingleQuote() {
    if (!currentAssetId) return;
    const reqAssetId = currentAssetId;
    const seq = ++requestSeqRef.current;
    setBusyAction("quote");
    setStatusMessage(null);
    try {
      const res = await api.refreshAssetQuote(reqAssetId);
      if (activeAssetRef.current !== reqAssetId || seq !== requestSeqRef.current) return;
      setChartData(res);
      setExplorationRules(null);
      await onRefreshBootstrap();
      if (activeAssetRef.current === reqAssetId) {
        setStatusMessage("已刷新当前标的规范化日线");
      }
    } catch (e) {
      if (activeAssetRef.current === reqAssetId) {
        setStatusMessage(e instanceof Error ? e.message : String(e));
      }
    } finally {
      if (seq === requestSeqRef.current) setBusyAction(null);
    }
  }

  async function handleSaveDecision(e: React.FormEvent) {
    e.preventDefault();
    if (!currentAssetId) return;
    const reqAssetId = currentAssetId;
    setBusyAction("decision");
    setStatusMessage(null);
    try {
      const res = await api.addDecision(reqAssetId, {
        action: decAction,
        execution: decExec,
        note: decNote.trim(),
        bar_date: chartData?.result?.bar_date,
        price_at_decision: chartData?.result?.close ?? null,
        exploration_strategies:
          attachExploration && explorationRules ? explorationRules : undefined,
      });
      if (activeAssetRef.current !== reqAssetId) return;
      setDecNote("");
      setChartData((prev) =>
        prev && prev.asset.id === reqAssetId ? { ...prev, decisions: res.decisions } : prev,
      );
      setStatusMessage("判断已追加保存并标注到 K 线");
    } catch (err) {
      if (activeAssetRef.current === reqAssetId) {
        setStatusMessage(err instanceof Error ? err.message : String(err));
      }
    } finally {
      setBusyAction(null);
    }
  }

  async function handleSaveTemplate() {
    if (!explorationRules?.length || !templateName.trim()) return;
    setBusyAction("template");
    try {
      await api.saveTemplate({ name: templateName.trim(), strategies: explorationRules });
      setTemplateName("");
      await onRefreshBootstrap();
      setStatusMessage("指标模板已保存");
    } catch (err) {
      setStatusMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyAction(null);
    }
  }

  const activeAsset = chartData?.asset ?? activeAssets.find((a) => a.id === currentAssetId) ?? null;
  const boundIds = new Set(activeAsset?.strategies ?? []);
  const isStale = chartData?.status === "error";

  return (
    <div className="flex-1 min-h-0 flex overflow-hidden relative">
      {/* LEFT: Watchlist (overlay sheet on <1024px, inline column on desktop) */}
      {watchlistOpen && (
        <aside
          className="w-60 shrink-0 border-r flex flex-col overflow-hidden max-lg:absolute max-lg:inset-y-0 max-lg:left-0 max-lg:z-30 max-lg:shadow-2xl"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
          aria-label="自选观察列表"
        >
          <div
            className="p-2.5 border-b flex flex-col gap-2"
            style={{ borderColor: "var(--app-border)" }}
          >
            <div className="flex items-center gap-1.5">
              <input
                value={wlSearch}
                onChange={(e) => setWlSearch(e.target.value)}
                placeholder="筛选自选（按 / 添加）"
                className="flex-1 min-w-0 h-7 px-2.5 rounded-md border text-xs bg-transparent outline-none"
                style={{
                  background: "var(--app-bg)",
                  borderColor: "var(--app-border)",
                }}
              />
              <button
                type="button"
                onClick={onOpenAddAsset}
                title="搜索或添加新标的 (/)"
                aria-label="添加标的"
                className="h-7 w-7 rounded-md border flex items-center justify-center shrink-0 cursor-pointer"
                style={{
                  background: "var(--app-elev)",
                  borderColor: "var(--app-border)",
                  color: "var(--app-text)",
                }}
              >
                <Plus className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="grid grid-cols-4 gap-1 text-[11px]">
              {(
                [
                  ["all", "全部"],
                  ["signal", "有信号"],
                  ["star", "重点"],
                  ["pending", "盘中"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setWlFilter(key)}
                  className="py-1 rounded-md cursor-pointer transition-colors"
                  style={{
                    background: wlFilter === key ? "var(--app-elev)" : "transparent",
                    color: wlFilter === key ? "var(--app-text)" : "var(--app-muted)",
                    fontWeight: wlFilter === key ? 600 : 400,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          <div className="flex-1 overflow-y-auto p-1.5 flex flex-col gap-1">
            {filteredGroupedAssets.map(([group, list]) => (
              <div key={group} className="mb-1">
                <div
                  className="px-2 pt-2 pb-1 text-[10px] font-semibold tracking-wider uppercase"
                  style={{ color: "var(--app-faint)" }}
                >
                  {group}
                </div>
                {list.map((a) => {
                  const selected = a.id === currentAssetId;
                  const chgPos = (a.change_pct ?? 0) >= 0;
                  return (
                    <div
                      key={a.id}
                      data-asset-card={a.id}
                      onClick={() => onSelectAsset(a.id)}
                      className="w-full text-left px-2.5 py-2 rounded-lg border mb-0.5 flex flex-col gap-1 cursor-pointer transition-colors"
                      style={{
                        background: selected ? "var(--app-elev)" : "transparent",
                        borderColor: selected ? "var(--app-border-strong)" : "transparent",
                      }}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="flex items-center gap-1 min-w-0">
                          <button
                            type="button"
                            onClick={(e) => handleToggleStar(a, e)}
                            title={a.priority ? "取消重点关注" : "标记重点关注"}
                            aria-label={a.priority ? `取消重点关注 ${a.label}` : `标记重点关注 ${a.label}`}
                            className="shrink-0 p-0.5 -ml-0.5 rounded cursor-pointer"
                            style={{ color: a.priority ? "var(--app-warn)" : "var(--app-faint)" }}
                          >
                            <Star
                              className="w-3 h-3"
                              fill={a.priority ? "currentColor" : "none"}
                            />
                          </button>
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onSelectAsset(a.id);
                            }}
                            className="text-xs font-semibold truncate text-left cursor-pointer"
                          >
                            {a.label}
                          </button>
                        </span>
                        <span className="text-xs font-semibold tabular shrink-0">
                          {a.close !== null ? a.close.toFixed(a.close < 10 ? 3 : 2) : "—"}
                        </span>
                      </div>

                      <div className="flex items-center justify-between gap-2">
                        <span
                          className="text-[10.5px] font-mono tabular"
                          style={{ color: "var(--app-faint)" }}
                        >
                          {a.code}
                        </span>
                        <span
                          className="text-[11px] font-medium tabular"
                          style={{
                            color:
                              a.change_pct === null
                                ? "var(--app-faint)"
                                : chgPos
                                  ? "var(--app-up)"
                                  : "var(--app-down)",
                          }}
                        >
                          {a.change_pct !== null
                            ? `${chgPos ? "+" : ""}${a.change_pct.toFixed(2)}%`
                            : "暂无行情"}
                        </span>
                      </div>

                      {a.status === "error" ? (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded w-fit font-medium"
                          style={{
                            background: "var(--app-warn-soft)",
                            color: "var(--app-warn)",
                          }}
                        >
                          采集异常 · 历史快照
                        </span>
                      ) : a.is_provisional ? (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded w-fit font-medium"
                          style={{
                            background: "var(--app-warn-soft)",
                            color: "var(--app-warn)",
                          }}
                        >
                          盘中未收盘
                        </span>
                      ) : a.latest_signal ? (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded w-fit font-medium truncate max-w-full"
                          style={{
                            background: "var(--app-up-soft)",
                            color: "var(--app-up)",
                          }}
                        >
                          {a.latest_signal.event_kind} · {a.latest_signal.event_date.slice(5)}
                        </span>
                      ) : !a.strategies.length ? (
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded w-fit"
                          style={{
                            background: "var(--app-accent-soft)",
                            color: "var(--app-accent)",
                          }}
                        >
                          仅观察行情
                        </span>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ))}
            {!filteredGroupedAssets.length && (
              <div className="text-center py-8 text-xs" style={{ color: "var(--app-muted)" }}>
                无匹配标的
              </div>
            )}
          </div>
        </aside>
      )}

      {/* CENTER: Hero + Single Clean Toolbar + Full-Height Chart */}
      <main className="flex-1 min-w-0 flex flex-col overflow-hidden">
        {/* Asset Hero */}
        <div
          className="px-4 py-2.5 border-b flex items-center justify-between gap-3 flex-wrap"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          <div className="flex items-baseline gap-2.5 min-w-0">
            <h1 className="text-base font-bold tracking-tight truncate">
              {activeAsset?.label ?? "请选择观察标的"}
            </h1>
            {activeAsset && (
              <>
                <span
                  className="text-xs font-mono tabular"
                  style={{ color: "var(--app-muted)" }}
                >
                  {activeAsset.code}
                </span>
                <span className="text-xs hidden md:inline" style={{ color: "var(--app-faint)" }}>
                  {activeAsset.market}
                </span>
              </>
            )}
          </div>

          {activeAsset && (
            <div className="flex items-center gap-2.5 tabular">
              <span className="text-lg font-bold tracking-tight">
                {chartData?.result?.close !== undefined
                  ? chartData.result.close.toFixed(chartData.result.close < 10 ? 3 : 2)
                  : "—"}
              </span>
              {chartData?.change_pct !== null && chartData?.change_pct !== undefined && (
                <span
                  className="text-xs font-semibold px-1.5 py-0.5 rounded"
                  style={{
                    background:
                      chartData.change_pct >= 0 ? "var(--app-up-soft)" : "var(--app-down-soft)",
                    color: chartData.change_pct >= 0 ? "var(--app-up)" : "var(--app-down)",
                  }}
                >
                  {chartData.change_pct >= 0 ? "+" : ""}
                  {chartData.change_pct.toFixed(2)}%
                </span>
              )}
              {chartData?.result?.bar_date && (
                <span
                  className="text-[11px] px-2 py-0.5 rounded-full border"
                  style={{ borderColor: "var(--app-border)", color: "var(--app-muted)" }}
                >
                  {chartData.result.bar_date.slice(5)}{" "}
                  {chartData.result.is_provisional ? "盘中未收盘" : "已收盘"}
                </span>
              )}
              <button
                type="button"
                disabled={busyAction !== null}
                onClick={handleRefreshSingleQuote}
                title="仅刷新当前标的行情"
                className="p-1.5 rounded-md border cursor-pointer"
                style={{
                  background: "var(--app-elev)",
                  borderColor: "var(--app-border)",
                  color: "var(--app-muted)",
                }}
              >
                <RefreshCw
                  className={`w-3.5 h-3.5 ${busyAction === "quote" ? "animate-spin" : ""}`}
                />
              </button>
            </div>
          )}
        </div>

        {/* Stale/Error Quote Banner */}
        {isStale && (
          <div
            className="px-4 py-2 text-xs flex items-center gap-2 border-b"
            style={{
              background: "var(--app-warn-soft)",
              color: "var(--app-warn)",
              borderColor: "var(--app-border)",
            }}
            role="status"
          >
            <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
            <span>
              {chartData?.fetch_summary?.label || "本次未能更新"} · 当前显示{" "}
              {chartData?.result?.bar_date || "历史"} 快照（{chartData?.error}）
            </span>
          </div>
        )}

        {/* Single Clean Toolbar */}
        <div
          className="px-3 py-1.5 border-b flex items-center justify-between gap-2 flex-wrap relative"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          <div className="flex items-center gap-2 flex-wrap">
            <div
              className="inline-flex p-0.5 rounded-md border text-xs"
              style={{ background: "var(--app-bg)", borderColor: "var(--app-border)" }}
            >
              {(
                [
                  ["day", "日线"],
                  ["week", "周线"],
                ] as const
              ).map(([p, label]) => (
                <button
                  key={p}
                  type="button"
                  onClick={() => setPeriod(p)}
                  className="px-2 py-0.5 rounded cursor-pointer"
                  style={{
                    background: period === p ? "var(--app-elev)" : "transparent",
                    color: period === p ? "var(--app-text)" : "var(--app-muted)",
                    fontWeight: period === p ? 600 : 400,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>

            <div
              className="inline-flex p-0.5 rounded-md border text-xs tabular"
              style={{ background: "var(--app-bg)", borderColor: "var(--app-border)" }}
            >
              {(
                [
                  ["63", "3M"],
                  ["126", "6M"],
                  ["252", "1Y"],
                  ["all", "全部"],
                ] as const
              ).map(([r, label]) => (
                <button
                  key={r}
                  type="button"
                  onClick={() => setRange(r)}
                  className="px-2 py-0.5 rounded cursor-pointer"
                  style={{
                    background: range === r ? "var(--app-elev)" : "transparent",
                    color: range === r ? "var(--app-text)" : "var(--app-muted)",
                    fontWeight: range === r ? 600 : 400,
                  }}
                >
                  {label}
                </button>
              ))}
            </div>

            <button
              type="button"
              onClick={() => setShowIndicatorMenu((v) => !v)}
              className="h-7 px-2.5 rounded-md border text-xs inline-flex items-center gap-1.5 cursor-pointer"
              style={{
                background: explorationRules ? "var(--app-blue-soft)" : "var(--app-elev)",
                borderColor: explorationRules ? "var(--app-blue)" : "var(--app-border)",
                color: explorationRules ? "var(--app-blue)" : "var(--app-text)",
              }}
            >
              <SlidersHorizontal className="w-3.5 h-3.5" />
              {explorationRules ? `探索中 (${explorationRules.length})` : "指标探索"}
            </button>

            {explorationRules && (
              <button
                type="button"
                onClick={restoreFormalChart}
                className="h-7 px-2 rounded-md border text-xs inline-flex items-center gap-1 cursor-pointer"
                style={{
                  background: "var(--app-bg)",
                  borderColor: "var(--app-border)",
                  color: "var(--app-muted)",
                }}
              >
                <RotateCcw className="w-3 h-3" />
                还原正式
              </button>
            )}
          </div>

          <div className="flex items-center gap-1.5 flex-wrap">
            <button
              type="button"
              onClick={() => setShowEvents((v) => !v)}
              className="h-7 px-2 rounded-md border text-xs cursor-pointer"
              style={{
                background: showEvents ? "var(--app-accent-soft)" : "var(--app-bg)",
                borderColor: showEvents ? "var(--app-accent)" : "var(--app-border)",
                color: showEvents ? "var(--app-accent)" : "var(--app-muted)",
              }}
            >
              事件
            </button>
            <button
              type="button"
              onClick={() => setShowDecisions((v) => !v)}
              className="h-7 px-2 rounded-md border text-xs cursor-pointer"
              style={{
                background: showDecisions ? "var(--app-accent-soft)" : "var(--app-bg)",
                borderColor: showDecisions ? "var(--app-accent)" : "var(--app-border)",
                color: showDecisions ? "var(--app-accent)" : "var(--app-muted)",
              }}
            >
              决策标注
            </button>
            <button
              type="button"
              onClick={() => setShowVolume((v) => !v)}
              className="h-7 px-2 rounded-md border text-xs cursor-pointer"
              style={{
                background: showVolume ? "var(--app-accent-soft)" : "var(--app-bg)",
                borderColor: showVolume ? "var(--app-accent)" : "var(--app-border)",
                color: showVolume ? "var(--app-accent)" : "var(--app-muted)",
              }}
            >
              VOL
            </button>

            {strategyEvents.length > 0 && (
              <div
                className="inline-flex items-center rounded-md border overflow-hidden"
                style={{ borderColor: "var(--app-border)", background: "var(--app-bg)" }}
              >
                <button
                  type="button"
                  onClick={() => jumpEvent(-1)}
                  title="上一策略事件"
                  className="h-7 px-1.5 cursor-pointer"
                  style={{ color: "var(--app-muted)" }}
                >
                  <ChevronLeft className="w-3.5 h-3.5" />
                </button>
                <span className="text-[11px] px-1 tabular" style={{ color: "var(--app-muted)" }}>
                  {eventCursor >= 0 ? `${eventCursor + 1}/${strategyEvents.length}` : `${strategyEvents.length} 事件`}
                </span>
                <button
                  type="button"
                  onClick={() => jumpEvent(1)}
                  title="下一策略事件"
                  className="h-7 px-1.5 cursor-pointer"
                  style={{ color: "var(--app-muted)" }}
                >
                  <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            )}
          </div>

          {/* Indicator Exploration & Templates Popover */}
          {showIndicatorMenu && (
            <div
              className="absolute left-3 top-10 z-30 w-80 rounded-xl border shadow-xl p-3 flex flex-col gap-3"
              style={{ background: "var(--app-panel)", borderColor: "var(--app-border-strong)" }}
            >
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold flex items-center gap-1.5">
                  <Layers className="w-3.5 h-3.5" style={{ color: "var(--app-accent)" }} />
                  临时指标探索（不影响自动监测）
                </span>
                <button
                  type="button"
                  onClick={() => setShowIndicatorMenu(false)}
                  className="text-xs cursor-pointer"
                  style={{ color: "var(--app-muted)" }}
                >
                  完成
                </button>
              </div>

              <div className="flex flex-wrap gap-1.5">
                {EXPLORATION_PRESETS.map((preset) => {
                  const active = (explorationRules ?? []).some((r) => r.id === preset.id);
                  return (
                    <button
                      key={preset.id}
                      type="button"
                      disabled={busyAction !== null}
                      onClick={() => {
                        const base = explorationRules ?? [];
                        const next = active
                          ? base.filter((r) => r.id !== preset.id)
                          : [...base, preset];
                        runExplorationPreview(next);
                      }}
                      className="px-2 py-1 rounded-md border text-xs cursor-pointer"
                      style={{
                        background: active ? "var(--app-blue-soft)" : "var(--app-elev)",
                        borderColor: active ? "var(--app-blue)" : "var(--app-border)",
                        color: active ? "var(--app-blue)" : "var(--app-text)",
                      }}
                    >
                      {active ? "✓ " : "+ "}
                      {preset.name}
                    </button>
                  );
                })}
              </div>

              {templates.length > 0 && (
                <div>
                  <div className="text-[11px] mb-1.5" style={{ color: "var(--app-muted)" }}>
                    已保存模板（跨标的一键应用）
                  </div>
                  <div className="flex flex-wrap gap-1.5">
                    {templates.map((tpl) => (
                      <button
                        key={tpl.template_id}
                        type="button"
                        onClick={() => runExplorationPreview(tpl.strategies)}
                        className="px-2 py-1 rounded-md border text-xs cursor-pointer"
                        style={{
                          background: "var(--app-bg)",
                          borderColor: "var(--app-border)",
                          color: "var(--app-text)",
                        }}
                      >
                        {tpl.name}
                      </button>
                    ))}
                  </div>
                </div>
              )}

              {explorationRules && explorationRules.length > 0 && (
                <div className="flex items-center gap-1.5 pt-2 border-t" style={{ borderColor: "var(--app-border)" }}>
                  <input
                    value={templateName}
                    onChange={(e) => setTemplateName(e.target.value)}
                    placeholder="另存为指标模板…"
                    className="flex-1 h-7 px-2 rounded-md border text-xs bg-transparent"
                    style={{ borderColor: "var(--app-border-strong)" }}
                  />
                  <button
                    type="button"
                    disabled={!templateName.trim() || busyAction !== null}
                    onClick={handleSaveTemplate}
                    className="h-7 px-2.5 rounded-md text-xs font-medium inline-flex items-center gap-1 cursor-pointer"
                    style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
                  >
                    <Save className="w-3 h-3" />
                    保存
                  </button>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Status Toast Strip (when present) */}
        {(statusMessage || chartError) && (
          <div
            className="px-4 py-1.5 text-xs flex items-center justify-between border-b"
            style={{
              background: chartError ? "var(--app-warn-soft)" : "var(--app-elev)",
              color: chartError ? "var(--app-warn)" : "var(--app-muted)",
              borderColor: "var(--app-border)",
            }}
            role="status"
          >
            <span>{chartError || statusMessage}</span>
            <button
              type="button"
              onClick={() => {
                setStatusMessage(null);
                setChartError(null);
              }}
              className="text-[11px] cursor-pointer"
            >
              收起
            </button>
          </div>
        )}

        {/* Chart Area */}
        <div className="flex-1 min-h-0 p-2.5 flex flex-col">
          {loadingChart && !chartData ? (
            <div
              className="flex-1 flex items-center justify-center rounded-lg border text-xs"
              style={{
                background: "var(--app-panel)",
                borderColor: "var(--app-border)",
                color: "var(--app-muted)",
              }}
            >
              正在加载规范化日线与指标…
            </div>
          ) : chartData ? (
            <CandleChart
              data={chartData}
              period={period}
              range={range}
              showVolume={showVolume}
              showEvents={showEvents}
              showDecisions={showDecisions}
              resolvedTheme={resolvedTheme}
              focusDate={focusDate}
              selectedEventIndex={eventCursor}
              onSelectEventIndex={setEventCursor}
            />
          ) : null}
        </div>
      </main>

      {/* RIGHT: Contextual Drawer (overlay sheet on <1024px, inline column on desktop) */}
      {drawerOpen && (
        <aside
          className="w-80 shrink-0 border-l flex flex-col overflow-hidden max-lg:absolute max-lg:inset-y-0 max-lg:right-0 max-lg:z-30 max-lg:shadow-2xl"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
          aria-label="标的上下文面板"
        >
          <div
            className="grid grid-cols-3 border-b text-xs"
            style={{ borderColor: "var(--app-border)" }}
          >
            {(
              [
                ["signals", "信号监测"],
                ["decisions", `决策 (${chartData?.decisions.length ?? 0})`],
                ["notes", `备忘 (${chartData?.notes.length ?? 0})`],
              ] as const
            ).map(([tab, label]) => (
              <button
                key={tab}
                type="button"
                onClick={() => setDrawerTab(tab)}
                className="py-2.5 text-center border-b-2 cursor-pointer transition-colors"
                style={{
                  borderBottomColor: drawerTab === tab ? "var(--app-accent)" : "transparent",
                  color: drawerTab === tab ? "var(--app-text)" : "var(--app-muted)",
                  fontWeight: drawerTab === tab ? 650 : 500,
                }}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-3">
            {/* TAB 1: SIGNALS & MONITORING */}
            {drawerTab === "signals" && (
              <>
                <div className="flex flex-col gap-2">
                  <div className="text-[11px] font-semibold" style={{ color: "var(--app-muted)" }}>
                    当前信号状态
                  </div>
                  {chartData?.result?.strategies?.length ? (
                    chartData.result.strategies.map((s) => (
                      <div
                        key={s.id}
                        className="p-2.5 rounded-lg border flex flex-col gap-1"
                        style={{
                          background: "var(--app-elev)",
                          borderColor: "var(--app-border)",
                        }}
                      >
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs font-semibold">{s.name}</span>
                          <span
                            className="text-[10px] px-1.5 py-0.5 rounded font-medium"
                            style={{
                              background: s.event
                                ? "var(--app-up-soft)"
                                : s.preview_event
                                  ? "var(--app-warn-soft)"
                                  : "var(--app-bg)",
                              color: s.event
                                ? "var(--app-up)"
                                : s.preview_event
                                  ? "var(--app-warn)"
                                  : "var(--app-muted)",
                            }}
                          >
                            {s.preview_event
                              ? `盘中 ${s.preview_event}`
                              : s.event
                                ? `${s.event} · ${s.event_date?.slice(5)}`
                                : "无新事件"}
                          </span>
                        </div>
                        {s.detail && (
                          <div className="text-[11px]" style={{ color: "var(--app-muted)" }}>
                            {s.detail}
                          </div>
                        )}
                      </div>
                    ))
                  ) : (
                    <div
                      className="p-3 rounded-lg border text-xs"
                      style={{
                        background: "var(--app-elev)",
                        borderColor: "var(--app-border)",
                        color: "var(--app-muted)",
                      }}
                    >
                      当前标的未绑定自动监测策略，仅观察日线行情。
                    </div>
                  )}
                </div>

                <div className="flex flex-col gap-2 pt-2 border-t" style={{ borderColor: "var(--app-border)" }}>
                  <div className="text-[11px] font-semibold" style={{ color: "var(--app-muted)" }}>
                    自动监测规则开关（下次检查生效）
                  </div>
                  {strategies.map((rule) => {
                    const bound = boundIds.has(rule.id);
                    return (
                      <label
                        key={rule.id}
                        className="flex items-center justify-between gap-2 px-2.5 py-2 rounded-lg border cursor-pointer"
                        style={{
                          background: bound ? "var(--app-accent-soft)" : "var(--app-bg)",
                          borderColor: bound ? "var(--app-accent)" : "var(--app-border)",
                        }}
                      >
                        <div className="min-w-0">
                          <div className="text-xs font-medium truncate">{rule.name}</div>
                          <div className="text-[10.5px]" style={{ color: "var(--app-muted)" }}>
                            {rule.group || rule.group_name || rule.kind}
                          </div>
                        </div>
                        <input
                          type="checkbox"
                          checked={bound}
                          disabled={busyAction !== null}
                          onChange={() => handleToggleMonitor(rule.id, bound)}
                          className="accent-[var(--app-accent)] w-4 h-4 cursor-pointer"
                        />
                      </label>
                    );
                  })}
                </div>
              </>
            )}

            {/* TAB 2: DECISIONS & TIMELINE */}
            {drawerTab === "decisions" && (
              <>
                <form
                  onSubmit={handleSaveDecision}
                  className="p-3 rounded-lg border flex flex-col gap-2.5"
                  style={{ background: "var(--app-elev)", borderColor: "var(--app-border)" }}
                >
                  <div className="flex items-center justify-between text-xs">
                    <span className="font-semibold">记录判断（无刷新追加）</span>
                    <span className="font-mono tabular" style={{ color: "var(--app-muted)" }}>
                      {chartData?.result?.bar_date?.slice(5) ?? "今日"}
                    </span>
                  </div>
                  <div className="grid grid-cols-4 gap-1">
                    {(["观望", "持有", "买入", "卖出"] as const).map((act) => (
                      <button
                        key={act}
                        type="button"
                        onClick={() => setDecAction(act)}
                        className="py-1.5 rounded-md border text-xs cursor-pointer"
                        style={{
                          background:
                            decAction === act ? "var(--app-accent-soft)" : "var(--app-bg)",
                          borderColor:
                            decAction === act ? "var(--app-accent)" : "var(--app-border)",
                          color: decAction === act ? "var(--app-accent)" : "var(--app-muted)",
                          fontWeight: decAction === act ? 650 : 400,
                        }}
                      >
                        {act}
                      </button>
                    ))}
                  </div>
                  <div className="flex items-center justify-between gap-2 text-xs">
                    <span style={{ color: "var(--app-muted)" }}>执行状态</span>
                    <select
                      value={decExec}
                      onChange={(e) => setDecExec(e.target.value as DecisionRecord["execution"])}
                      className="px-2 py-1 rounded-md border text-xs"
                      style={{
                        background: "var(--app-bg)",
                        borderColor: "var(--app-border-strong)",
                      }}
                    >
                      <option value="未执行">未执行</option>
                      <option value="已提交">已提交</option>
                    </select>
                  </div>
                  <textarea
                    rows={3}
                    value={decNote}
                    onChange={(e) => setDecNote(e.target.value)}
                    placeholder="为什么行动，或为什么继续等待？保存后自动标注到对应 K 线。"
                    className="w-full p-2 rounded-md border text-xs bg-transparent outline-none resize-none"
                    style={{
                      background: "var(--app-bg)",
                      borderColor: "var(--app-border)",
                    }}
                  />
                  {explorationRules && explorationRules.length > 0 && (
                    <label className="flex items-center gap-1.5 text-[11px] cursor-pointer">
                      <input
                        type="checkbox"
                        checked={attachExploration}
                        onChange={(e) => setAttachExploration(e.target.checked)}
                      />
                      <span style={{ color: "var(--app-blue)" }}>
                        附带当前 {explorationRules.length} 项探索指标作为依据
                      </span>
                    </label>
                  )}
                  <button
                    type="submit"
                    disabled={busyAction !== null}
                    className="w-full py-1.5 rounded-md text-xs font-semibold cursor-pointer"
                    style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
                  >
                    {busyAction === "decision" ? "保存中…" : "保存本次判断"}
                  </button>
                </form>

                <div className="flex flex-col gap-2">
                  <div className="text-[11px] font-semibold" style={{ color: "var(--app-muted)" }}>
                    该标的跨日决策时间线（点击定位 K 线）
                  </div>
                  {chartData?.decisions.length ? (
                    chartData.decisions.map((d) => (
                      <button
                        key={d.id}
                        type="button"
                        onClick={() => onFocusDateChange(d.bar_date)}
                        className="text-left p-2.5 rounded-lg border-l-2 border cursor-pointer transition-colors"
                        style={{
                          background: "var(--app-bg)",
                          borderColor: "var(--app-border)",
                          borderLeftColor:
                            d.action === "买入"
                              ? "var(--app-up)"
                              : d.action === "卖出"
                                ? "var(--app-down)"
                                : "var(--app-accent)",
                        }}
                      >
                        <div className="flex items-center justify-between gap-2 text-xs mb-1">
                          <span className="font-semibold">
                            {d.action}
                            {d.price_at_decision !== null && (
                              <span className="ml-1.5 font-mono tabular font-normal">
                                @{d.price_at_decision.toFixed(d.price_at_decision < 10 ? 3 : 2)}
                              </span>
                            )}
                          </span>
                          <span className="text-[11px] font-mono tabular" style={{ color: "var(--app-muted)" }}>
                            {d.bar_date}
                          </span>
                        </div>
                        <div className="text-[11.5px] leading-relaxed" style={{ color: "var(--app-muted)" }}>
                          {d.note || "（无补充备注）"}
                        </div>
                        <div className="mt-1 flex items-center gap-2 text-[10px]" style={{ color: "var(--app-faint)" }}>
                          <span>{d.execution}</span>
                          {d.has_exploration && (
                            <span style={{ color: "var(--app-blue)" }}>含探索依据</span>
                          )}
                        </div>
                      </button>
                    ))
                  ) : (
                    <div className="text-xs py-4 text-center" style={{ color: "var(--app-muted)" }}>
                      暂无历史决策，不操作也是一种决定。
                    </div>
                  )}
                </div>
              </>
            )}

            {/* TAB 3: RELATED NOTES */}
            {drawerTab === "notes" && (
              <div className="flex flex-col gap-2">
                {chartData?.notes.length ? (
                  chartData.notes.map((n) => (
                    <button
                      key={n.id}
                      type="button"
                      onClick={() => onOpenNote(n.id)}
                      className="text-left p-3 rounded-lg border cursor-pointer"
                      style={{ background: "var(--app-elev)", borderColor: "var(--app-border)" }}
                    >
                      <div className="flex items-center justify-between gap-2 mb-1">
                        <span className="text-xs font-semibold truncate">{n.title}</span>
                        <span
                          className="text-[10px] px-1.5 py-0.5 rounded shrink-0"
                          style={{
                            background: n.pinned ? "var(--app-accent-soft)" : "var(--app-blue-soft)",
                            color: n.pinned ? "var(--app-accent)" : "var(--app-blue)",
                          }}
                        >
                          {n.pinned ? "全局置顶" : "关联本标的"}
                        </span>
                      </div>
                      <p
                        className="text-[11.5px] line-clamp-3 m-0"
                        style={{ color: "var(--app-muted)" }}
                      >
                        {n.markdown.replace(/^#.*\n+/, "").slice(0, 120)}
                      </p>
                    </button>
                  ))
                ) : (
                  <div className="text-xs py-6 text-center" style={{ color: "var(--app-muted)" }}>
                    暂无与本标的关联的备忘录。
                  </div>
                )}
                <button
                  type="button"
                  onClick={() => onOpenNote()}
                  className="py-2 rounded-md border text-xs font-medium inline-flex items-center justify-center gap-1.5 cursor-pointer"
                  style={{
                    background: "var(--app-bg)",
                    borderColor: "var(--app-border)",
                    color: "var(--app-accent)",
                  }}
                >
                  <BookOpen className="w-3.5 h-3.5" />
                  打开备忘录管理
                </button>
              </div>
            )}
          </div>
        </aside>
      )}
    </div>
  );
}
