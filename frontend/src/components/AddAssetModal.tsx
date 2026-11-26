import { useEffect, useState } from "react";
import { Plus, Search, X } from "lucide-react";
import { api } from "../api";
import type { CatalogItem } from "../types";

interface AddAssetModalProps {
  open: boolean;
  version: number;
  onClose: () => void;
  onAdded: (assetId: string) => void;
}

export function AddAssetModal({ open, version, onClose, onAdded }: AddAssetModalProps) {
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<CatalogItem[]>([]);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showManual, setShowManual] = useState(false);

  const [manualLabel, setManualLabel] = useState("");
  const [manualCode, setManualCode] = useState("");
  const [manualProfile, setManualProfile] = useState("sse:cn_equity");
  const [manualKind, setManualKind] = useState<"etf" | "index" | "spot">("etf");
  const [manualSecid, setManualSecid] = useState("");
  const [manualExpected, setManualExpected] = useState("");

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    api
      .catalog(query)
      .then((res) => {
        if (!cancelled) setItems(res.assets);
      })
      .catch((e) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      cancelled = true;
    };
  }, [open, query]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  async function handleAddCatalog(item: CatalogItem) {
    setBusyId(item.id);
    setError(null);
    try {
      const res = await api.addAsset(version, { id: item.id });
      onAdded(res.selected);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusyId(null);
    }
  }

  async function handleManualSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusyId("__manual__");
    setError(null);
    try {
      const [provider, session_profile] = manualProfile.split(":");
      const res = await api.addAsset(version, {
        label: manualLabel.trim(),
        code: manualCode.trim(),
        provider,
        session_profile,
        kind: manualKind,
        secid: manualSecid.trim() || undefined,
        expected_name: manualExpected.trim() || undefined,
      });
      onAdded(res.selected);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-16 px-4"
      style={{ background: "rgba(0,0,0,0.52)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-lg rounded-xl border shadow-2xl overflow-hidden"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border-strong)" }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label="搜索或添加标的"
      >
        <div
          className="flex items-center gap-2.5 px-4 py-3 border-b"
          style={{ borderColor: "var(--app-border)" }}
        >
          <Search className="w-4 h-4 shrink-0" style={{ color: "var(--app-muted)" }} />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索名称、代码或别名（如 标普、黄金、红利低波、515450）…"
            className="flex-1 bg-transparent text-sm outline-none"
            style={{ color: "var(--app-text)" }}
          />
          <button
            type="button"
            onClick={onClose}
            className="p-1 rounded-md hover:opacity-80"
            style={{ color: "var(--app-muted)" }}
            aria-label="关闭搜索"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {error && (
          <div
            className="px-4 py-2 text-xs border-b"
            style={{
              background: "var(--app-warn-soft)",
              color: "var(--app-warn)",
              borderColor: "var(--app-border)",
            }}
          >
            {error}
          </div>
        )}

        <div className="max-h-72 overflow-y-auto p-2 flex flex-col gap-1">
          {items.map((item) => (
            <div
              key={item.id}
              className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg"
              style={{ background: "var(--app-elev)" }}
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-semibold truncate">{item.label}</span>
                  <span
                    className="text-xs font-mono tabular"
                    style={{ color: "var(--app-muted)" }}
                  >
                    {item.code}
                  </span>
                </div>
                <div className="text-[11px]" style={{ color: "var(--app-muted)" }}>
                  {item.market || item.provider} · {item.group_name || item.group || "未分组"}
                </div>
              </div>
              <button
                type="button"
                disabled={busyId !== null}
                onClick={() => handleAddCatalog(item)}
                className="px-2.5 py-1 rounded-md text-xs font-medium shrink-0 cursor-pointer"
                style={{
                  background: item.in_workspace ? "var(--app-bg)" : "var(--app-btn)",
                  color: item.in_workspace ? "var(--app-muted)" : "var(--app-btn-text)",
                  border: item.in_workspace ? "1px solid var(--app-border)" : "none",
                }}
              >
                {busyId === item.id
                  ? "核验中…"
                  : item.in_workspace
                    ? "查看"
                    : "加入观察"}
              </button>
            </div>
          ))}
          {!items.length && (
            <div className="text-center py-6 text-xs" style={{ color: "var(--app-muted)" }}>
              目录中暂无匹配标的，可展开下方手动添加。
            </div>
          )}
        </div>

        <div className="border-t px-4 py-2.5" style={{ borderColor: "var(--app-border)" }}>
          <button
            type="button"
            onClick={() => setShowManual((v) => !v)}
            className="text-xs font-medium inline-flex items-center gap-1 cursor-pointer"
            style={{ color: "var(--app-accent)" }}
          >
            <Plus className="w-3.5 h-3.5" />
            {showManual ? "收起手动添加" : "没有找到？手动验证并添加标的"}
          </button>

          {showManual && (
            <form onSubmit={handleManualSubmit} className="mt-3 grid grid-cols-2 gap-2.5 text-xs">
              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>名称</span>
                <input
                  required
                  value={manualLabel}
                  onChange={(e) => setManualLabel(e.target.value)}
                  className="px-2.5 py-1.5 rounded-md border bg-transparent"
                  style={{ borderColor: "var(--app-border-strong)" }}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>代码</span>
                <input
                  required
                  value={manualCode}
                  onChange={(e) => setManualCode(e.target.value)}
                  className="px-2.5 py-1.5 rounded-md border bg-transparent font-mono"
                  style={{ borderColor: "var(--app-border-strong)" }}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>数据源与市场</span>
                <select
                  value={manualProfile}
                  onChange={(e) => setManualProfile(e.target.value)}
                  className="px-2 py-1.5 rounded-md border"
                  style={{
                    background: "var(--app-bg)",
                    borderColor: "var(--app-border-strong)",
                  }}
                >
                  <option value="sse:cn_equity">沪市 ETF / 股票 · 上交所</option>
                  <option value="szse:cn_equity">深市 ETF / 股票 · 深交所</option>
                  <option value="yahoo:us_equity">美国指数 · Yahoo</option>
                  <option value="eastmoney:cn_equity">中国指数 / ETF · 东财</option>
                  <option value="eastmoney:sge_gold">上金所黄金 · 东财</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>品种类型</span>
                <select
                  value={manualKind}
                  onChange={(e) => setManualKind(e.target.value as "etf" | "index" | "spot")}
                  className="px-2 py-1.5 rounded-md border"
                  style={{
                    background: "var(--app-bg)",
                    borderColor: "var(--app-border-strong)",
                  }}
                >
                  <option value="etf">场内 ETF</option>
                  <option value="index">指数</option>
                  <option value="spot">现货</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>数据源精确名称</span>
                <input
                  value={manualExpected}
                  onChange={(e) => setManualExpected(e.target.value)}
                  placeholder="上交所/深交所/东财必填"
                  className="px-2.5 py-1.5 rounded-md border bg-transparent"
                  style={{ borderColor: "var(--app-border-strong)" }}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span style={{ color: "var(--app-muted)" }}>东财 secid（可选）</span>
                <input
                  value={manualSecid}
                  onChange={(e) => setManualSecid(e.target.value)}
                  placeholder="如 1.518800"
                  className="px-2.5 py-1.5 rounded-md border bg-transparent font-mono"
                  style={{ borderColor: "var(--app-border-strong)" }}
                />
              </label>
              <div className="col-span-2 flex justify-end pt-1">
                <button
                  type="submit"
                  disabled={busyId !== null}
                  className="px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer"
                  style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
                >
                  {busyId === "__manual__" ? "联网校验中…" : "验证并加入列表"}
                </button>
              </div>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
