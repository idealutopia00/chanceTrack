import type { CandleBar, MacdPoint, OverlaySeries } from "../types";

function isoWeekKey(dateStr: string): string {
  const [y, m, d] = dateStr.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  const dayNum = dt.getUTCDay() || 7;
  dt.setUTCDate(dt.getUTCDate() + 4 - dayNum);
  const yearStart = new Date(Date.UTC(dt.getUTCFullYear(), 0, 1));
  const weekNo = Math.ceil(((dt.getTime() - yearStart.getTime()) / 86400000 + 1) / 7);
  return `${dt.getUTCFullYear()}-W${String(weekNo).padStart(2, "0")}`;
}

export function aggregateWeekly(
  candles: CandleBar[],
  overlays: OverlaySeries[],
  macd: MacdPoint[],
) {
  const dayToWeekTime = new Map<string, string>();
  const weeklyCandles: CandleBar[] = [];
  let currentKey = "";
  let bucket: CandleBar[] = [];

  const flush = () => {
    if (!bucket.length) return;
    const last = bucket[bucket.length - 1];
    const allHaveVol = bucket.every((b) => typeof b.volume === "number");
    const vol = allHaveVol
      ? bucket.reduce((sum, b) => sum + (b.volume ?? 0), 0)
      : null;
    weeklyCandles.push({
      time: last.time,
      open: bucket[0].open,
      high: Math.max(...bucket.map((b) => b.high)),
      low: Math.min(...bucket.map((b) => b.low)),
      close: last.close,
      volume: vol,
    });
    for (const b of bucket) {
      dayToWeekTime.set(b.time, last.time);
    }
  };

  for (const bar of candles) {
    const key = isoWeekKey(bar.time);
    if (key !== currentKey) {
      flush();
      currentKey = key;
      bucket = [bar];
    } else {
      bucket.push(bar);
    }
  }
  flush();

  const weekLastDates = new Set(weeklyCandles.map((b) => b.time));
  const weeklyOverlays = overlays.map((ov) => ({
    ...ov,
    points: (ov.points || []).filter((p) => weekLastDates.has(p.time)),
  }));
  const weeklyMacd = macd.filter((p) => weekLastDates.has(p.time));

  return {
    candles: weeklyCandles,
    overlays: weeklyOverlays,
    macd: weeklyMacd,
    dayToWeekTime,
  };
}
