import { useEffect, useRef, useState } from "react";
import { Copy, Plus, Trash2, X } from "lucide-react";
import { api } from "../api";
import type { StrategyRule } from "../types";

interface StrategiesViewProps {
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

function defaultParams(kind: string): Record<string, number | string> {
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
    default:
      return { fast: 12, slow: 26, signal: 9 };
  }
}

export function StrategiesView({ onConfigChanged }: StrategiesViewProps) {
  const [version, setVersion] = useState(1);
  const [rules, setRules] = useState<StrategyRule[]>([]);
  const [editing, setEditing] = useState<StrategyRule | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  async function load() {
    const res = await api.strategies();
    setVersion(res.version);
    setRules(res.strategies);
  }

  useEffect(() => {
    load();
  }, []);

  function closeEditor() {
    setEditing(null);
    setError(null);
    requestAnimationFrame(() => triggerRef.current?.focus());
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!editing) return;
    setBusy(true);
    setError(null);
    try {
      await api.saveStrategy({ version, op: "upsert", strategy: editing });
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
      setEditing(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-6xl w-full mx-auto flex flex-col gap-5">
      <div className="flex items-center justify-between gap-4">
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
              params: defaultParams("macd"),
            });
          }}
          className="px-3 py-1.5 rounded-lg text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer"
          style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
        >
          <Plus className="w-3.5 h-3.5" />
          新建策略规则
        </button>
      </div>

      {error && !editing && (
        <div
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
        <table className="w-full border-collapse text-xs tabular">
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
                  {r.params
                    ? Object.entries(r.params)
                        .map(([k, v]) => `${k}=${v}`)
                        .join(" / ")
                    : r.trigger
                      ? `trigger=${r.trigger}`
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
                className="p-2.5 rounded-lg text-xs"
                style={{ background: "var(--app-warn-soft)", color: "var(--app-warn)" }}
              >
                {error}
              </div>
            )}

            <label className="flex flex-col gap-1 text-xs">
              <span style={{ color: "var(--app-muted)" }}>规则名称</span>
              <input
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
                  onChange={(e) =>
                    setEditing({
                      ...editing,
                      kind: e.target.value,
                      params: defaultParams(e.target.value),
                    })
                  }
                  className="px-2.5 py-2 rounded-lg border"
                  style={{
                    background: "var(--app-bg)",
                    borderColor: "var(--app-border-strong)",
                  }}
                >
                  <option value="macd">MACD 交叉</option>
                  <option value="ma">双均线交叉</option>
                  <option value="boll_break">布林突破</option>
                  <option value="boll_return">布林回归</option>
                  <option value="donchian">唐奇安通道</option>
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

            {editing.params && Object.keys(editing.params).length > 0 && (
              <div className="flex flex-col gap-2.5 pt-2 border-t" style={{ borderColor: "var(--app-border)" }}>
                <span className="text-xs font-semibold" style={{ color: "var(--app-muted)" }}>
                  核心参数
                </span>
                <div className="grid grid-cols-2 gap-3 text-xs">
                  {Object.entries(editing.params).map(([key, val]) => (
                    <label key={key} className="flex flex-col gap-1">
                      <span style={{ color: "var(--app-muted)" }}>{key}</span>
                      <input
                        type="number"
                        step={["stddev", "multiple", "threshold"].includes(key) ? "0.1" : "1"}
                        value={val}
                        onChange={(e) =>
                          setEditing({
                            ...editing,
                            params: {
                              ...editing.params,
                              [key]: ["stddev", "multiple", "threshold"].includes(key)
                                ? Number(e.target.value)
                                : Math.round(Number(e.target.value)),
                            },
                          })
                        }
                        className="px-2.5 py-1.5 rounded-lg border bg-transparent font-mono tabular"
                        style={{ borderColor: "var(--app-border-strong)" }}
                      />
                    </label>
                  ))}
                </div>
              </div>
            )}

            <div className="mt-auto pt-4 border-t flex items-center justify-between gap-2" style={{ borderColor: "var(--app-border)" }}>
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
