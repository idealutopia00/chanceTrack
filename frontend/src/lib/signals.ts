import type { AssetSummary, SignalDirection } from "../types";

const BULLISH_RE =
  /金叉|买入观察|向上突破|下轨外回归|短线回调|回调修复|释放 · 向上|相对转强/;
const BEARISH_RE =
  /死叉|卖出观察|向下突破|上轨外回归|通道退出|趋势失效|跟踪退出|释放 · 向下/;

export function resolveEventDirection(
  event: string | null | undefined,
  explicit?: SignalDirection | null,
): SignalDirection {
  if (explicit === "bullish" || explicit === "bearish" || explicit === "neutral") {
    return explicit;
  }
  if (!event) return "neutral";
  if (BULLISH_RE.test(event)) return "bullish";
  if (BEARISH_RE.test(event)) return "bearish";
  return "neutral";
}

export const SIGNAL_DIRECTION_STYLES: Record<
  SignalDirection,
  { text: string; soft: string; label: string }
> = {
  bullish: {
    text: "var(--app-up)",
    soft: "var(--app-up-soft)",
    label: "偏多",
  },
  bearish: {
    text: "var(--app-down)",
    soft: "var(--app-down-soft)",
    label: "偏空",
  },
  neutral: {
    text: "var(--app-blue)",
    soft: "var(--app-blue-soft)",
    label: "中性",
  },
};

export function getChartDirectionColor(
  direction: SignalDirection,
  palette: { up: string; down: string; blue: string },
): string {
  if (direction === "bullish") return palette.up;
  if (direction === "bearish") return palette.down;
  return palette.blue;
}

export function getDirectionGlyph(
  direction: SignalDirection,
  provisional = false,
): string {
  if (direction === "bullish") return provisional ? "△" : "▲";
  if (direction === "bearish") return provisional ? "▽" : "▼";
  return provisional ? "◇" : "◆";
}

export function hasAssetSignalSummary(asset: AssetSummary): boolean {
  if (asset.archived || !asset.watchlisted) return false;
  const rows = asset.strategy_summaries;
  if (Array.isArray(rows)) {
    return rows.some((s) => Boolean(s.event_kind && s.event_date));
  }
  return Boolean(asset.latest_signal);
}

export function hasRecordedSignal(asset: AssetSummary): boolean {
  return hasAssetSignalSummary(asset);
}
