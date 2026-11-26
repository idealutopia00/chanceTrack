import { useCallback, useEffect, useRef, useState } from "react";
import { Copy, Plus, Trash2, X } from "lucide-react";
import { api } from "../api";
import type { AssetSummary, StrategyRule } from "../types";

interface StrategiesViewProps {
  assets: AssetSummary[];
  onConfigChanged: () => Promise<void>;
}

const KIND_LABELS: Record<string, string> = {
  macd: "MACD 交叉",
  ma: "双均线交叉",
  boll_break: "布林突破",
  boll_return: "布林回归",
  donchian: "唐奇安通道",
  pullback: "趋势回调",
  trailing_exit: "跟踪止损",
  squeeze: "布林压缩",
  relative_strength: "相对强弱",
  combination: "组合策略",
};

const FLOAT_KEYS = new Set(["stddev", "multiple", "threshold"]);

function defaultParams(kind: string, defaultBenchmark = "sp500"): Record<string, number | string> {
  switch (kind) {
    case "macd":
      return { fast: 12, slow: 26, signal: 9 };
    case "ma":
      return { fast: 20, slow: 60 };
    case "boll_break":
    case "boll_return":
      return { period: 20, stddev: 2 };
    case "donchian":
      return { entry: 20, exit: 10 };
    case "pullback":
      return { trend: 200, recovery: 20, rsi: 14, threshold: 35 };
    case "trailing_exit":
      return { entry: 20, period: 14, multiple: 2.5 };
    case "squeeze":
      return { period: 20, stddev: 2, multiple: 1.5, range: "true_range" };
    case "relative_strength":
      return { trend: 200, relative_period: 20, benchmark: defaultBenchmark };
    case "combination":
      return {};
    default:
      return { fast: 12, slow: 26, signal: 9 };
  }
}

export function StrategiesView({ assets, onConfigChanged }: StrategiesViewProps) {
  const [version, setVersion] = useState(1);
  const [rules, setRules] = useState<StrategyRule[]>([]);
  const [editing, setEditing] = useState<StrategyRule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  const usdBenchmarks = assets.filter(
    (a) => !a.archived && a.provider === "yahoo" && a.session_profile === "us_equity",
  );
  const nonComboRules = rules.filter((r) => r.kind !== "combination");
  const bollRules = rules.filter((r) => r.kind === "boll_break" || r.kind === "boll_return");

  const load = useCallback(async () => {
    try {
      const res = await api.strategies();
      setVersion(res.version);
      setRules(res.strategies);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const closeEditor = useCallback(() => {
    setEditing(null);
    setError(null);
    requestAnimationFrame(() => triggerRef.current?.focus());
  }, []);

  useEffect(() => {
    if (!editing) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeEditor();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [editing, closeEditor]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      const payloadRule: StrategyRule = structuredClone(editing);
      if (payloadRule.kind === "combination") {
        delete payloadRule.params;
        if (!payloadRule.filter) {
          delete payloadRule.filter;
          delete payloadRule.band;
        }
      }
      await api.saveStrategy({ version, op: "upsert", strategy: payloadRule });
      await load();
      await onConfigChanged();
      closeEditor();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(rule: StrategyRule) {
    setBusy(true);
    setError(null);
    try {
      await api.saveStrategy({ version, op: "delete", strategy_id: rule.id });
      await load();
      await onConfigChanged();
      closeEditor();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-4 sm:p-6 max-w-6xl w-full mx-auto flex flex-col gap-5">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-bold tracking-tight">策略规则库</h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--app-muted)" }}>
            集中维护所有指标与组合规则，一目了然查看每条规则绑定的标的与参数修订
          </p>
        </div>
        <button
          type="button"
          onClick={(e) => {
            triggerRef.current = e.currentTarget;
            setEditing({
              id: "",
              name: "新策略规则",
              kind: "macd",
              group: "趋势",
              params: defaultParams("macd", usdBenchmarks[0]?.id || "sp500"),
            });
          }}
          className="px-3 py-1.5 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer shrink-0"
          style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
        >
          <Plus className="w-3.5 h-3.5" />
          新建策略规则
        </button>
      </div>

      {error && !editing && (
        <div
          role="alert"
          className="px-4 py-2.5 rounded-lg text-xs"
          style={{ background: "var(--app-warn-soft)", color: "var(--app-warn)" }}
        >
          {error}
        </div>
      )}

      <div
        className="rounded-xl border overflow-hidden"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
      >
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs tabular min-w-[640px]">
            <thead>
              <tr
                className="text-left border-b"
                style={{
                  borderColor: "var(--app-border)",
                  background: "var(--app-elev)",
                  color: "var(--app-muted)",
                }}
              >
                <th className="py-2.5 px-4 font-semibold">规则名称</th>
                <th className="py-2.5 px-4 font-semibold">类型 / 分组</th>
                <th className="py-2.5 px-4 font-semibold">参数摘要</th>
                <th className="py-2.5 px-4 font-semibold">受影响标的</th>
                <th className="py-2.5 px-4 font-semibold">修订</th>
                <th className="py-2.5 px-4 font-semibold text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {rules.map((r) => (
                <tr
                  key={r.id}
                  className="border-b transition-colors hover:bg-[var(--app-hover)]"
                  style={{ borderColor: "var(--app-border)" }}
                >
                  <td className="py-3 px-4 font-semibold">{r.name}</td>
                  <td className="py-3 px-4" style={{ color: "var(--app-muted)" }}>
                    {KIND_LABELS[r.kind] || r.kind} · {r.group || r.group_name || "未分组"}
                  </td>
                  <td className="py-3 px-4 font-mono text-[11px]" style={{ color: "var(--app-muted)" }}>
                    {r.params && Object.keys(r.params).length > 0
                      ? Object.entries(r.params)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" / ")
                      : r.trigger
                        ? `trigger=${r.trigger}${r.filter ? ` · ${r.filter}(${r.band || ""})` : ""}`
                        : "—"}
                  </td>
                  <td className="py-3 px-4">
                    <div className="flex flex-wrap gap-1">
                      {r.assets?.length ? (
                        r.assets.map((a) => (
                          <span
                            key={a.id}
                            className="px-2 py-0.5 rounded-full text-[10.5px] font-medium"
                            style={{
                              background: "var(--app-accent-soft)",
                              color: "var(--app-accent)",
                            }}
                          >
                            {a.label}
                          </span>
                        ))
                      ) : (
                        <span style={{ color: "var(--app-faint)" }}>未绑定（仅探索可用）</span>
                      )}
                    </div>
                  </td>
                  <td className="py-3 px-4 font-mono" style={{ color: "var(--app-faint)" }}>
                    v{r.revision ?? 1}
                  </td>
                  <td className="py-3 px-4 text-right">
                    <button
                      type="button"
                      onClick={(e) => {
                        triggerRef.current = e.currentTarget;
                        setEditing(structuredClone(r));
                        setError(null);
                      }}
                      className="px-2.5 py-1 rounded-md border text-xs cursor-pointer"
                      style={{
                        background: "var(--app-elev)",
                        borderColor: "var(--app-border)",
                        color: "var(--app-text)",
                      }}
                    >
                      编辑
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Slide-over Strategy Editor */}
      {editing && (
        <div
          className="fixed inset-0 z-50 flex justify-end"
          style={{ background: "rgba(0,0,0,0.48)" }}
          onClick={closeEditor}
        >
          <form
            onSubmit={handleSave}
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-md h-full border-l p-5 flex flex-col gap-4 overflow-y-auto"
            style={{ background: "var(--app-panel)", borderColor: "var(--app-border-strong)" }}
            role="dialog"
            aria-modal="true"
            aria-label="编辑策略规则"
          >
            <div className="flex items-center justify-between">
              <h2 className="text-base font-bold">
                {editing.id ? `编辑规则 · ${editing.name}` : "新建策略规则"}
              </h2>
              <button
                type="button"
                onClick={closeEditor}
                className="p-1 rounded-md cursor-pointer"
                style={{ color: "var(--app-muted)" }}
                aria-label="关闭编辑"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {error && (
              <div
                role="alert"
                className="p-2.5 rounded-lg text-xs"
                style={{ background: "var(--app-warn-soft)", color: "var(--app-warn)" }}
              >
                {error}
              </div>
            )}

            <label className="flex flex-col gap-1 text-xs">
              <span style={{ color: "var(--app-muted)" }}>规则名称</span>
              <input
                autoFocus
                required
                value={editing.name}
                onChange={(e) => setEditing({ ...editing, name: e.target.value })}
                className="px-3 py-2 rounded-lg border bg-transparent text-sm"
                style={{ borderColor: "var(--app-border-strong)" }}
              />
            </label>

            <div className="grid grid-cols-2 gap-3 text-xs">
              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>规则类型</span>
                <select
                  disabled={Boolean(editing.id)}
                  value={editing.kind}
                  onChange={(e) => {
                    const nextKind = e.target.value;
                    if (nextKind === "combination") {
                      setEditing({
                        ...editing,
                        kind: nextKind,
                        params: {},
                        trigger: nonComboRules[0]?.id || "",
                        filter: "",
                        band: bollRules[0]?.id || "",
                      });
                    } else {
                      setEditing({
                        ...editing,
                        kind: nextKind,
                        params: defaultParams(nextKind, usdBenchmarks[0]?.id || "sp500"),
                      });
                    }
                  }}
                  className="px-2.5 py-2 rounded-lg border"
                  style={{
                    background: "var(--app-bg)",
                    borderColor: "var(--app-border-strong)",
                  }}
                >
                  {Object.entries(KIND_LABELS).map(([k, label]) => (
                    <option key={k} value={k}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>展示分组</span>
                <input
                  value={editing.group || editing.group_name || ""}
                  onChange={(e) =>
                    setEditing({ ...editing, group: e.target.value, group_name: e.target.value })
                  }
                  className="px-2.5 py-2 rounded-lg border bg-transparent"
                  style={{ borderColor: "var(--app-border-strong)" }}
                />
              </label>
            </div>

            {editing.kind === "combination" ? (
              <div className="flex flex-col gap-3 pt-2 border-t text-xs" style={{ borderColor: "var(--app-border)" }}>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--app-muted)" }}>主触发策略</span>
                  <select
                    value={editing.trigger || ""}
                    onChange={(e) => setEditing({ ...editing, trigger: e.target.value })}
                    className="px-2.5 py-2 rounded-lg border"
                    style={{ background: "var(--app-bg)", borderColor: "var(--app-border-strong)" }}
                  >
                    {nonComboRules.map((r) => (
                      <option key={r.id} value={r.id}>
                        {r.name} ({KIND_LABELS[r.kind] || r.kind})
                      </option>
                    ))}
                  </select>
                </label>
                <label className="flex flex-col gap-1">
                  <span style={{ color: "var(--app-muted)" }}>布林带过滤条件</span>
                  <select
                    value={editing.filter || ""}
                    onChange={(e) =>
                      setEditing({
                        ...editing,
                        filter: e.target.value || undefined,
                        band: e.target.value ? editing.band || bollRules[0]?.id : undefined,
                      })
                    }
                    className="px-2.5 py-2 rounded-lg border"
                    style={{ background: "var(--app-bg)", borderColor: "var(--app-border-strong)" }}
                  >
                    <option value="">无过滤</option>
                    <option value="above_mid">收盘高于布林中轨</option>
                    <option value="below_mid">收盘低于布林中轨</option>
                  </select>
                </label>
                {editing.filter && (
                  <label className="flex flex-col gap-1">
                    <span style={{ color: "var(--app-muted)" }}>引用布林配置</span>
                    <select
                      value={editing.band || ""}
                      onChange={(e) => setEditing({ ...editing, band: e.target.value })}
                      className="px-2.5 py-2 rounded-lg border"
                      style={{ background: "var(--app-bg)", borderColor: "var(--app-border-strong)" }}
                    >
                      {bollRules.map((r) => (
                        <option key={r.id} value={r.id}>
                          {r.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )}
              </div>
            ) : (
              editing.params &&
              Object.keys(editing.params).length > 0 && (
                <div
                  className="flex flex-col gap-2.5 pt-2 border-t"
                  style={{ borderColor: "var(--app-border)" }}
                >
                  <span className="text-xs font-semibold" style={{ color: "var(--app-muted)" }}>
                    核心参数
                  </span>
                  <div className="grid grid-cols-2 gap-3 text-xs">
                    {Object.entries(editing.params).map(([key, val]) => {
                      if (key === "range") {
                        return (
                          <label key={key} className="flex flex-col gap-1">
                            <span style={{ color: "var(--app-muted)" }}>肯特纳振幅口径</span>
                            <select
                              value={String(val)}
                              onChange={(e) =>
                                setEditing({
                                  ...editing,
                                  params: { ...editing.params, range: e.target.value },
                                })
                              }
                              className="px-2.5 py-1.5 rounded-lg border"
                              style={{
                                background: "var(--app-bg)",
                                borderColor: "var(--app-border-strong)",
                              }}
                            >
                              <option value="true_range">真实波幅 (TR)</option>
                              <option value="high_low">高低价差 (H-L)</option>
                            </select>
                          </label>
                        );
                      }
                      if (key === "benchmark") {
                        return (
                          <label key={key} className="flex flex-col gap-1">
                            <span style={{ color: "var(--app-muted)" }}>对比指数基准</span>
                            <select
                              value={String(val)}
                              onChange={(e) =>
                                setEditing({
                                  ...editing,
                                  params: { ...editing.params, benchmark: e.target.value },
                                })
                              }
                              className="px-2.5 py-1.5 rounded-lg border"
                              style={{
                                background: "var(--app-bg)",
                                borderColor: "var(--app-border-strong)",
                              }}
                            >
                              {usdBenchmarks.length ? (
                                usdBenchmarks.map((b) => (
                                  <option key={b.id} value={b.id}>
                                    {b.label} ({b.code})
                                  </option>
                                ))
                              ) : (
                                <option value={String(val)}>{String(val)}</option>
                              )}
                            </select>
                          </label>
                        );
                      }
                      const isFloat = FLOAT_KEYS.has(key);
                      return (
                        <label key={key} className="flex flex-col gap-1">
                          <span style={{ color: "var(--app-muted)" }}>{key}</span>
                          <input
                            type="number"
                            step={isFloat ? "0.1" : "1"}
                            value={val}
                            onChange={(e) => {
                              const parsed = Number(e.target.value);
                              if (!Number.isFinite(parsed)) return;
                              setEditing({
                                ...editing,
                                params: {
                                  ...editing.params,
                                  [key]: isFloat ? parsed : Math.round(parsed),
                                },
                              });
                            }}
                            className="px-2.5 py-1.5 rounded-lg border bg-transparent font-mono tabular"
                            style={{ borderColor: "var(--app-border-strong)" }}
                          />
                        </label>
                      );
                    })}
                  </div>
                </div>
              )
            )}

            <div
              className="mt-auto pt-4 border-t flex items-center justify-between gap-2"
              style={{ borderColor: "var(--app-border)" }}
            >
              <div className="flex items-center gap-2">
                {editing.id && (
                  <>
                    <button
                      type="button"
                      onClick={() =>
                        setEditing({
                          ...structuredClone(editing),
                          id: "",
                          name: `${editing.name} 副本`,
                        })
                      }
                      className="px-2.5 py-1.5 rounded-lg border text-xs inline-flex items-center gap-1 cursor-pointer"
                      style={{ borderColor: "var(--app-border)", color: "var(--app-muted)" }}
                    >
                      <Copy className="w-3.5 h-3.5" />
                      复制
                    </button>
                    {(!editing.assets || editing.assets.length === 0) && (
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => handleDelete(editing)}
                        className="px-2.5 py-1.5 rounded-lg border text-xs inline-flex items-center gap-1 cursor-pointer"
                        style={{ borderColor: "var(--app-border)", color: "var(--app-up)" }}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                        删除
                      </button>
                    )}
                  </>
                )}
              </div>
              <button
                type="submit"
                disabled={busy}
                className="px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer"
                style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
              >
                {busy ? "保存中…" : "保存规则"}
              </button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
}
