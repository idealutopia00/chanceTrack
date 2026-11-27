import { useEffect, useState } from "react";
import { ArrowUpRight, CheckCircle2, Clock, AlertCircle } from "lucide-react";
import { api } from "../api";
import { resolveEventDirection, SIGNAL_DIRECTION_STYLES } from "../lib/signals";
import type { CheckRunSummary, SignalEventRecord } from "../types";

interface SignalsViewProps {
  onJumpToAsset: (assetId: string, date?: string) => void;
}

export function SignalsView({ onJumpToAsset }: SignalsViewProps) {
  const [signals, setSignals] = useState<SignalEventRecord[]>([]);
  const [totalSignals, setTotalSignals] = useState(0);
  const [runs, setRuns] = useState<CheckRunSummary[]>([]);
  const [filter, setFilter] = useState<"all" | "notified" | "fresh">("all");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    Promise.all([api.signals({ limit: 150 }), api.runs(20)])
      .then(([sRes, rRes]) => {
        if (!cancelled) {
          setSignals(sRes.signals);
          setTotalSignals(sRes.total ?? sRes.signals.length);
          setRuns(rRes.runs);
        }
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const filtered = signals.filter((s) => {
    if (filter === "notified") return Boolean(s.notified_at);
    if (filter === "fresh") return !s.historical_reference;
    return true;
  });

  const latestRun = runs[0];

  return (
    <div className="flex-1 overflow-y-auto p-6 max-w-6xl w-full mx-auto flex flex-col gap-5">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-lg font-bold tracking-tight">信号流与检查批次</h1>
          <p className="text-xs mt-0.5" style={{ color: "var(--app-muted)" }}>
            直接查询规范化 signal_events 与 check_runs，点击任意信号跳转工作台对应 K 线
          </p>
        </div>
        <div
          className="inline-flex p-0.5 rounded-lg border text-xs"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          {(
            [
              ["all", "全部事件"],
              ["fresh", "仅生效后新信号"],
              ["notified", "已发送通知"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              onClick={() => setFilter(k)}
              className="px-3 py-1 rounded-md cursor-pointer"
              style={{
                background: filter === k ? "var(--app-elev)" : "transparent",
                color: filter === k ? "var(--app-text)" : "var(--app-muted)",
                fontWeight: filter === k ? 600 : 400,
              }}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div
          role="alert"
          className="px-4 py-2.5 rounded-lg text-xs"
          style={{ background: "var(--app-warn-soft)", color: "var(--app-warn)" }}
        >
          加载失败：{error}
        </div>
      )}

      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 tabular">
        <div
          className="p-3.5 rounded-xl border"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          <div className="text-xs" style={{ color: "var(--app-muted)" }}>
            累计记录信号
          </div>
          <div className="text-xl font-bold mt-1">{totalSignals}</div>
        </div>
        <div
          className="p-3.5 rounded-xl border"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          <div className="text-xs" style={{ color: "var(--app-muted)" }}>
            最近检查批次
          </div>
          <div className="text-xl font-bold mt-1">
            {latestRun ? `#${latestRun.id}` : "—"}
          </div>
        </div>
        <div
          className="p-3.5 rounded-xl border"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          <div className="text-xs" style={{ color: "var(--app-muted)" }}>
            上次正常覆盖
          </div>
          <div className="text-xl font-bold mt-1" style={{ color: "var(--app-down)" }}>
            {latestRun ? `${latestRun.ok_assets} / ${latestRun.total_assets}` : "—"}
          </div>
        </div>
        <div
          className="p-3.5 rounded-xl border"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          <div className="text-xs" style={{ color: "var(--app-muted)" }}>
            上次采集异常
          </div>
          <div
            className="text-xl font-bold mt-1"
            style={{ color: latestRun?.error_assets ? "var(--app-warn)" : "var(--app-muted)" }}
          >
            {latestRun ? latestRun.error_assets : 0}
          </div>
        </div>
      </div>

      {/* Structured Signal Feed */}
      <section
        className="rounded-xl border overflow-hidden"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
      >
        <div
          className="px-4 py-2.5 border-b text-xs font-semibold"
          style={{ borderColor: "var(--app-border)", background: "var(--app-elev)" }}
        >
          策略确认事件流
        </div>
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-xs tabular">
            <thead>
              <tr
                className="text-left border-b"
                style={{ borderColor: "var(--app-border)", color: "var(--app-muted)" }}
              >
                <th className="py-2.5 px-4 font-semibold">信号日</th>
                <th className="py-2.5 px-4 font-semibold">标的</th>
                <th className="py-2.5 px-4 font-semibold">策略</th>
                <th className="py-2.5 px-4 font-semibold">事件</th>
                <th className="py-2.5 px-4 font-semibold">说明</th>
                <th className="py-2.5 px-4 font-semibold">状态</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={6} className="py-8 text-center" style={{ color: "var(--app-muted)" }}>
                    加载中…
                  </td>
                </tr>
              ) : filtered.length ? (
                filtered.map((s) => (
                  <tr
                    key={s.id}
                    tabIndex={0}
                    role="button"
                    onClick={() => onJumpToAsset(s.asset_id, s.event_date)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        onJumpToAsset(s.asset_id, s.event_date);
                      }
                    }}
                    className="border-b cursor-pointer transition-colors hover:bg-[var(--app-hover)]"
                    style={{ borderColor: "var(--app-border)" }}
                  >
                    <td className="py-2.5 px-4 font-mono">{s.event_date}</td>
                    <td className="py-2.5 px-4">
                      <span className="font-semibold">{s.asset_label}</span>{" "}
                      <span className="font-mono" style={{ color: "var(--app-faint)" }}>
                        {s.asset_code}
                      </span>
                    </td>
                    <td className="py-2.5 px-4">{s.strategy_name || s.strategy_version}</td>
                    <td
                      className="py-2.5 px-4 font-semibold"
                      style={{
                        color:
                          SIGNAL_DIRECTION_STYLES[
                            resolveEventDirection(s.event_kind, s.direction)
                          ].text,
                      }}
                    >
                      {s.event_kind}
                    </td>
                    <td className="py-2.5 px-4 max-w-xs truncate" style={{ color: "var(--app-muted)" }}>
                      {s.detail || "—"}
                    </td>
                    <td className="py-2.5 px-4">
                      <span className="inline-flex items-center gap-1">
                        {s.notified_at ? (
                          <span
                            className="px-2 py-0.5 rounded text-[10.5px] font-medium"
                            style={{
                              background: "var(--app-accent-soft)",
                              color: "var(--app-accent)",
                            }}
                          >
                            已通知
                          </span>
                        ) : s.historical_reference ? (
                          <span
                            className="px-2 py-0.5 rounded text-[10.5px]"
                            style={{ background: "var(--app-elev)", color: "var(--app-muted)" }}
                          >
                            启用前参考
                          </span>
                        ) : (
                          <span
                            className="px-2 py-0.5 rounded text-[10.5px]"
                            style={{ background: "var(--app-blue-soft)", color: "var(--app-blue)" }}
                          >
                            已记录
                          </span>
                        )}
                        <ArrowUpRight className="w-3 h-3" style={{ color: "var(--app-faint)" }} />
                      </span>
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan={6} className="py-8 text-center" style={{ color: "var(--app-muted)" }}>
                    暂无匹配的确认信号事件。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {/* Lightweight Check Runs History */}
      <section
        className="rounded-xl border overflow-hidden"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
      >
        <div
          className="px-4 py-2.5 border-b text-xs font-semibold"
          style={{ borderColor: "var(--app-border)", background: "var(--app-elev)" }}
        >
          最近检查批次（轻量记录）
        </div>
        <div className="divide-y" style={{ borderColor: "var(--app-border)" }}>
          {runs.map((r) => (
            <div
              key={r.id}
              className="px-4 py-2.5 flex items-center justify-between gap-3 text-xs tabular"
              style={{ borderColor: "var(--app-border)" }}
            >
              <div className="flex items-center gap-2.5">
                {r.error_assets > 0 ? (
                  <AlertCircle className="w-4 h-4 shrink-0" style={{ color: "var(--app-warn)" }} />
                ) : (
                  <CheckCircle2 className="w-4 h-4 shrink-0" style={{ color: "var(--app-down)" }} />
                )}
                <span className="font-mono font-semibold">#{r.id}</span>
                <span className="font-mono" style={{ color: "var(--app-muted)" }}>
                  {r.created_at.slice(0, 16).replace("T", " ")}
                </span>
                <span
                  className="px-1.5 py-0.5 rounded text-[10.5px]"
                  style={{ background: "var(--app-elev)", color: "var(--app-muted)" }}
                >
                  {r.kind === "scheduled" ? "定时检查" : "手动检查"}
                </span>
              </div>
              <div className="flex items-center gap-3" style={{ color: "var(--app-muted)" }}>
                <span>
                  正常 {r.ok_assets}/{r.total_assets}
                </span>
                <span>新事件 {r.new_events}</span>
                {r.notification_sent_at && (
                  <span className="inline-flex items-center gap-1" style={{ color: "var(--app-accent)" }}>
                    <Clock className="w-3 h-3" />
                    已发通知
                  </span>
                )}
              </div>
            </div>
          ))}
          {!runs.length && (
            <div className="py-6 text-center text-xs" style={{ color: "var(--app-muted)" }}>
              暂无检查批次记录。
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
