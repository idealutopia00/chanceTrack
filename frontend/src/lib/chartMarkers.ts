import type {
  IPrimitivePaneRenderer,
  IPrimitivePaneView,
  ISeriesPrimitive,
  PrimitiveHoveredItem,
  SeriesAttachedParameter,
  SeriesType,
  Time,
} from "lightweight-charts";
import type { CandleBar, ChartEvent, DecisionRecord } from "../types";

type CanvasRenderingTarget2D = Parameters<IPrimitivePaneRenderer["draw"]>[0];

export type MarkerDirection = "bullish" | "bearish" | "neutral";

export interface MarkerThemePalette {
  up: string;
  down: string;
  panel: string;
  text: string;
  muted: string;
  border: string;
  warn: string;
  blue: string;
  accent: string;
  pillBg: string;
  pillText: string;
  focusRing: string;
  solidRim: string;
  stem: string;
}

export interface IndexedChartEvent extends ChartEvent {
  eventKey: string;
  mappedTime: string;
  resolvedDirection: MarkerDirection;
}

export interface MarkerCluster {
  id: string;
  barTime: string;
  side: "above" | "below";
  direction: MarkerDirection;
  events: IndexedChartEvent[];
  provisional: boolean;
}

export interface RenderedMarkerGeometry {
  clusterId: string;
  barTime: string;
  side: "above" | "below";
  direction: MarkerDirection;
  provisional: boolean;
  eventCount: number;
  x: number;
  wickY: number;
  tipY: number;
  baseY: number;
  clearancePx: number;
  pillRect: { x: number; y: number; w: number; h: number } | null;
  tier: number;
  folded: boolean;
  selected: boolean;
  labelText: string;
  hitRect: { x: number; y: number; w: number; h: number };
}

export interface MarkerLayoutSummary {
  visibleCount: number;
  foldedCount: number;
  rendered: RenderedMarkerGeometry[];
}

const BULLISH_RE =
  /金叉|买入观察|向上突破|下轨外回归|短线回调|回调修复|释放 · 向上|相对转强/;
const BEARISH_RE =
  /死叉|卖出观察|向下突破|上轨外回归|通道退出|趋势失效|跟踪退出|释放 · 向下/;

export function resolveMarkerDirection(
  event: string | null | undefined,
  explicit?: MarkerDirection | null,
): MarkerDirection {
  if (explicit === "bullish" || explicit === "bearish" || explicit === "neutral") {
    return explicit;
  }
  if (!event) return "neutral";
  if (BULLISH_RE.test(event)) return "bullish";
  if (BEARISH_RE.test(event)) return "bearish";
  return "neutral";
}

export function sortEventsChronologically(rawEvents: ChartEvent[]): ChartEvent[] {
  return rawEvents
    .map((ev, origIdx) => ({ ev, origIdx }))
    .sort((a, b) => {
      const da = a.ev.date || a.ev.time || "";
      const db = b.ev.date || b.ev.time || "";
      if (da !== db) return da.localeCompare(db);
      return a.origIdx - b.origIdx;
    })
    .map((item) => item.ev);
}

export function indexChartEvents(
  rawEvents: ChartEvent[],
  dayToWeekTime: Map<string, string>,
  barSet: Set<string>,
): IndexedChartEvent[] {
  const ordered = sortEventsChronologically(rawEvents);
  const seen = new Map<string, number>();
  const indexed: IndexedChartEvent[] = [];
  for (const ev of ordered) {
    const evDate = ev.date || ev.time;
    if (!evDate) continue;
    const mapped = dayToWeekTime.get(evDate) ?? evDate;
    if (!barSet.has(mapped)) continue;
    const baseKey = `${evDate}::${ev.strategy_id || ev.name || ""}::${ev.event}`;
    const count = seen.get(baseKey) ?? 0;
    seen.set(baseKey, count + 1);
    const eventKey = count === 0 ? baseKey : `${baseKey}#${count}`;
    indexed.push({
      ...ev,
      eventKey,
      mappedTime: mapped,
      resolvedDirection: resolveMarkerDirection(ev.event, ev.direction),
    });
  }
  return indexed;
}

export function buildMarkerClusters(events: IndexedChartEvent[]): MarkerCluster[] {
  const map = new Map<string, MarkerCluster>();
  for (const ev of events) {
    const side: "above" | "below" = ev.resolvedDirection === "bullish" ? "below" : "above";
    const id = `${ev.mappedTime}:${side}`;
    const existing = map.get(id);
    if (existing) {
      existing.events.push(ev);
      if (ev.provisional) existing.provisional = true;
      if (existing.direction === "neutral" && ev.resolvedDirection !== "neutral") {
        existing.direction = ev.resolvedDirection;
      }
    } else {
      map.set(id, {
        id,
        barTime: ev.mappedTime,
        side,
        direction: ev.resolvedDirection,
        events: [ev],
        provisional: Boolean(ev.provisional),
      });
    }
  }
  return Array.from(map.values()).sort((a, b) => a.barTime.localeCompare(b.barTime));
}

function directionColor(dir: MarkerDirection, pal: MarkerThemePalette): string {
  if (dir === "bullish") return pal.up;
  if (dir === "bearish") return pal.down;
  return pal.blue;
}

function directionGlyph(dir: MarkerDirection, provisional: boolean): string {
  if (dir === "bullish") return provisional ? "△" : "▲";
  if (dir === "bearish") return provisional ? "▽" : "▼";
  return provisional ? "◇" : "◆";
}

function formatClusterLabel(
  cluster: MarkerCluster,
  selectedEventKey: string | null,
): string {
  const selectedEv = selectedEventKey
    ? cluster.events.find((e) => e.eventKey === selectedEventKey)
    : null;
  const primary = selectedEv ?? cluster.events[0];
  const prefix = primary.provisional ? "盘中·" : "";
  const name = primary.event;
  if (cluster.events.length > 1) {
    if (selectedEv) {
      const idx = cluster.events.indexOf(selectedEv) + 1;
      return `${prefix}${name} (${idx}/${cluster.events.length})`;
    }
    return `${prefix}${name} +${cluster.events.length - 1}`;
  }
  return `${prefix}${name}`;
}

function drawRoundedRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  w: number,
  h: number,
  r: number,
) {
  const radius = Math.min(r, w / 2, h / 2);
  ctx.beginPath();
  ctx.moveTo(x + radius, y);
  ctx.arcTo(x + w, y, x + w, y + h, radius);
  ctx.arcTo(x + w, y + h, x, y + h, radius);
  ctx.arcTo(x, y + h, x, y, radius);
  ctx.arcTo(x, y, x + w, y, radius);
  ctx.closePath();
}

function drawDirectionShape(
  ctx: CanvasRenderingContext2D,
  x: number,
  tipY: number,
  baseY: number,
  halfW: number,
  dir: MarkerDirection,
) {
  ctx.beginPath();
  if (dir === "neutral") {
    const midY = (tipY + baseY) / 2;
    const ry = Math.abs(baseY - tipY) / 2;
    ctx.moveTo(x, midY - ry);
    ctx.lineTo(x + halfW, midY);
    ctx.lineTo(x, midY + ry);
    ctx.lineTo(x - halfW, midY);
    ctx.closePath();
  } else {
    ctx.moveTo(x, tipY);
    ctx.lineTo(x + halfW, baseY);
    ctx.lineTo(x - halfW, baseY);
    ctx.closePath();
  }
}

function rectsOverlap(
  a: { x: number; y: number; w: number; h: number },
  b: { x: number; y: number; w: number; h: number },
  margin = 4,
): boolean {
  return !(
    a.x + a.w + margin <= b.x ||
    b.x + b.w + margin <= a.x ||
    a.y + a.h + 2 <= b.y ||
    b.y + b.h + 2 <= a.y
  );
}

const WICK_GAP = 10;
const ARROW_H = 10;
const ARROW_HALF_W = 5.5;
const PILL_H = 19;
const PILL_GAP = 5;
const TIER_STEP = 22;
const FONT_SPEC = '600 10.5px -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang SC", sans-serif';

interface DecisionMark {
  barTime: string;
  action: DecisionRecord["action"];
  shortText: string;
}

export class StrategyMarkersPrimitive implements ISeriesPrimitive<Time> {
  private attachedParam: SeriesAttachedParameter<Time, SeriesType> | null = null;
  private clusters: MarkerCluster[] = [];
  private decisions: DecisionMark[] = [];
  private barMap = new Map<string, CandleBar>();
  private palette: MarkerThemePalette;
  private selectedClusterId: string | null = null;
  private selectedEventKey: string | null = null;
  private compactLabels = false;
  private showEvents = true;
  private showDecisions = true;
  private lastLayout: MarkerLayoutSummary = { visibleCount: 0, foldedCount: 0, rendered: [] };
  private onLayoutChange?: (summary: MarkerLayoutSummary) => void;
  private readonly paneView: IPrimitivePaneView;

  constructor(
    palette: MarkerThemePalette,
    onLayoutChange?: (summary: MarkerLayoutSummary) => void,
  ) {
    this.palette = palette;
    this.onLayoutChange = onLayoutChange;
    this.paneView = {
      zOrder: () => "top",
      renderer: (): IPrimitivePaneRenderer => ({
        draw: (target: CanvasRenderingTarget2D) => {
          this.draw(target);
        },
      }),
    };
  }

  attached(param: SeriesAttachedParameter<Time, SeriesType>) {
    this.attachedParam = param;
    param.requestUpdate();
  }

  detached() {
    this.attachedParam = null;
  }

  updateData(opts: {
    clusters: MarkerCluster[];
    decisions: DecisionRecord[];
    candles: CandleBar[];
    dayToWeekTime: Map<string, string>;
    showEvents: boolean;
    showDecisions: boolean;
    compactLabels: boolean;
    selectedClusterId: string | null;
    selectedEventKey: string | null;
    palette: MarkerThemePalette;
  }) {
    this.clusters = opts.clusters;
    this.barMap = new Map(opts.candles.map((c) => [c.time, c]));
    const actionShort: Record<DecisionRecord["action"], string> = {
      买入: "买",
      卖出: "卖",
      持有: "持",
      观望: "观",
    };
    const decs: DecisionMark[] = [];
    for (const d of opts.decisions) {
      const mapped = opts.dayToWeekTime.get(d.bar_date) ?? d.bar_date;
      if (!this.barMap.has(mapped)) continue;
      decs.push({ barTime: mapped, action: d.action, shortText: actionShort[d.action] });
    }
    this.decisions = decs;
    this.showEvents = opts.showEvents;
    this.showDecisions = opts.showDecisions;
    this.compactLabels = opts.compactLabels;
    this.selectedClusterId = opts.selectedClusterId;
    this.selectedEventKey = opts.selectedEventKey;
    this.palette = opts.palette;
    this.attachedParam?.requestUpdate();
  }

  updatePalette(palette: MarkerThemePalette) {
    this.palette = palette;
    this.attachedParam?.requestUpdate();
  }

  getLastLayout(): MarkerLayoutSummary {
    return this.lastLayout;
  }

  paneViews(): readonly IPrimitivePaneView[] {
    return [this.paneView];
  }

  hitTest(x: number, y: number): PrimitiveHoveredItem | null {
    const items = this.lastLayout.rendered;
    let best: { id: string; dist: number } | null = null;
    for (const m of items) {
      // Exact pill hit
      if (
        m.pillRect &&
        x >= m.pillRect.x - 3 &&
        x <= m.pillRect.x + m.pillRect.w + 3 &&
        y >= m.pillRect.y - 3 &&
        y <= m.pillRect.y + m.pillRect.h + 3
      ) {
        const cx = m.pillRect.x + m.pillRect.w / 2;
        const cy = m.pillRect.y + m.pillRect.h / 2;
        const dist = Math.hypot(x - cx, y - cy);
        if (!best || dist < best.dist) best = { id: m.clusterId, dist };
      }
      // Generous arrow hit
      const arrowCy = (m.tipY + m.baseY) / 2;
      const dist = Math.hypot(x - m.x, y - arrowCy);
      if (dist <= 16) {
        if (!best || dist < best.dist) best = { id: m.clusterId, dist };
      }
    }
    if (!best) return null;
    return {
      externalId: best.id,
      zOrder: "top",
      cursorStyle: "pointer",
      distance: best.dist,
      hitTestPriority: 2,
      itemType: "marker",
    };
  }

  private draw(target: CanvasRenderingTarget2D) {
    const param = this.attachedParam;
    if (!param) return;
    const { chart, series } = param;
    const pal = this.palette;

    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      const width = mediaSize.width;
      const height = mediaSize.height;
      if (width <= 0 || height <= 0) return;

      const rendered: RenderedMarkerGeometry[] = [];
      let foldedCount = 0;

      if (this.showEvents && this.clusters.length > 0) {
        ctx.save();
        ctx.font = FONT_SPEC;
        ctx.textBaseline = "middle";
        ctx.textAlign = "left";

        interface Candidate {
          cluster: MarkerCluster;
          x: number;
          wickY: number;
          tipY: number;
          baseY: number;
          dirSign: 1 | -1;
          selected: boolean;
          labelText: string;
          pillW: number;
        }

        const candidates: Candidate[] = [];
        for (const cluster of this.clusters) {
          const bar = this.barMap.get(cluster.barTime);
          if (!bar) continue;
          const x = chart.timeScale().timeToCoordinate(cluster.barTime as Time);
          if (x === null || x < -20 || x > width + 20) continue;
          const anchorPrice = cluster.side === "below" ? bar.low : bar.high;
          const wickY = series.priceToCoordinate(anchorPrice);
          if (wickY === null) continue;
          const dirSign: 1 | -1 = cluster.side === "below" ? 1 : -1;
          const rawTipY = wickY + dirSign * WICK_GAP;
          const tipY = Math.max(10, Math.min(height - 10, rawTipY));
          const baseY = tipY + dirSign * ARROW_H;
          const selected = cluster.id === this.selectedClusterId;
          const labelText = formatClusterLabel(cluster, selected ? this.selectedEventKey : null);
          const glyph = directionGlyph(cluster.direction, cluster.provisional);
          const textW = ctx.measureText(`${glyph} ${labelText}`).width;
          const pillW = Math.ceil(textW + 12);
          candidates.push({
            cluster,
            x,
            wickY,
            tipY,
            baseY,
            dirSign,
            selected,
            labelText,
            pillW,
          });
        }

        // Priority order for pill placement: selected first, then newest to oldest
        const ordered = [...candidates].sort((a, b) => {
          if (a.selected !== b.selected) return a.selected ? -1 : 1;
          return b.x - a.x;
        });

        const placedPills: { side: "above" | "below"; rect: { x: number; y: number; w: number; h: number } }[] = [];
        const geometryById = new Map<string, RenderedMarkerGeometry>();

        for (const cand of ordered) {
          const allowPill = !this.compactLabels || cand.selected;
          let chosenRect: { x: number; y: number; w: number; h: number } | null = null;
          let chosenTier = -1;
          let folded = false;

          if (allowPill) {
            const xCandidates = [
              Math.max(4, Math.min(width - cand.pillW - 4, cand.x - cand.pillW / 2)),
              Math.max(4, Math.min(width - cand.pillW - 4, cand.x - cand.pillW + 8)),
              Math.max(4, Math.min(width - cand.pillW - 4, cand.x - 8)),
            ];
            for (const tier of [0, 1, 2]) {
              const offset = PILL_GAP + tier * TIER_STEP;
              const topY =
                cand.dirSign === 1
                  ? cand.baseY + offset
                  : cand.baseY - offset - PILL_H;
              if (topY < 3 || topY + PILL_H > height - 3) {
                continue;
              }
              for (const desiredX of xCandidates) {
                const rect = { x: desiredX, y: topY, w: cand.pillW, h: PILL_H };
                const collides = placedPills.some(
                  (p) => p.side === cand.cluster.side && rectsOverlap(p.rect, rect, 5),
                );
                if (!collides) {
                  chosenRect = rect;
                  chosenTier = tier;
                  placedPills.push({ side: cand.cluster.side, rect });
                  break;
                }
              }
              if (chosenRect) break;
            }
            if (!chosenRect) {
              folded = true;
              foldedCount += 1;
            }
          } else {
            folded = true;
            foldedCount += 1;
          }

          const clearancePx = Math.abs(cand.tipY - cand.wickY);
          const hitTop = Math.min(cand.tipY, cand.baseY) - 4;
          geometryById.set(cand.cluster.id, {
            clusterId: cand.cluster.id,
            barTime: cand.cluster.barTime,
            side: cand.cluster.side,
            direction: cand.cluster.direction,
            provisional: cand.cluster.provisional,
            eventCount: cand.cluster.events.length,
            x: cand.x,
            wickY: cand.wickY,
            tipY: cand.tipY,
            baseY: cand.baseY,
            clearancePx,
            pillRect: chosenRect,
            tier: chosenTier,
            folded,
            selected: cand.selected,
            labelText: cand.labelText,
            hitRect: {
              x: cand.x - 10,
              y: hitTop,
              w: 20,
              h: ARROW_H + 8,
            },
          });
        }

        // Draw non-selected first, selected last (on top)
        const drawOrder = [...candidates].sort((a, b) => Number(a.selected) - Number(b.selected));
        for (const cand of drawOrder) {
          const geom = geometryById.get(cand.cluster.id);
          if (!geom) continue;
          rendered.push(geom);

          const col = directionColor(cand.cluster.direction, pal);
          const dirSign = cand.dirSign;

          // 1. Hairline connector stem from wick gap to arrow tip (explicit bar correspondence)
          const stemStart = cand.wickY + dirSign * 3;
          const stemEnd = cand.tipY - dirSign * 1.5;
          if ((dirSign === 1 && stemEnd > stemStart) || (dirSign === -1 && stemEnd < stemStart)) {
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(cand.x, stemStart);
            ctx.lineTo(cand.x, stemEnd);
            ctx.strokeStyle = pal.panel;
            ctx.lineWidth = 3;
            ctx.stroke();
            ctx.strokeStyle = cand.selected ? pal.focusRing : pal.stem;
            ctx.lineWidth = 1;
            ctx.stroke();
            ctx.restore();
          }

          // 2. If pill is staggered on Tier 1, draw thin vertical connector from arrow base to pill
          if (geom.pillRect && geom.tier > 0) {
            const pillEdgeY = dirSign === 1 ? geom.pillRect.y : geom.pillRect.y + geom.pillRect.h;
            const targetX = Math.max(
              geom.pillRect.x + 6,
              Math.min(geom.pillRect.x + geom.pillRect.w - 6, cand.x),
            );
            ctx.save();
            ctx.beginPath();
            ctx.moveTo(cand.x, cand.baseY);
            ctx.lineTo(targetX, pillEdgeY);
            ctx.strokeStyle = pal.panel;
            ctx.lineWidth = 2.5;
            ctx.stroke();
            ctx.setLineDash([2, 2]);
            ctx.strokeStyle = col;
            ctx.lineWidth = 1;
            ctx.stroke();
            ctx.restore();
          }

          // 3. Arrow / Diamond shape: direction = shape, confirmation = solid vs hollow
          ctx.save();
          ctx.lineJoin = "round";
          drawDirectionShape(ctx, cand.x, cand.tipY, cand.baseY, ARROW_HALF_W, cand.cluster.direction);
          // Outer contrast halo separates marker from same-color candles & overlays
          ctx.strokeStyle = pal.panel;
          ctx.lineWidth = cand.cluster.provisional ? 4.5 : 3.6;
          ctx.stroke();

          if (cand.cluster.provisional) {
            // Hollow outlined marker for provisional / intraday events
            ctx.fillStyle = pal.panel;
            ctx.fill();
            ctx.strokeStyle = col;
            ctx.lineWidth = 2.2;
            ctx.stroke();
          } else {
            // Solid filled marker for confirmed events
            ctx.fillStyle = col;
            ctx.fill();
            ctx.strokeStyle = pal.solidRim;
            ctx.lineWidth = 0.9;
            ctx.stroke();
          }

          if (cand.selected) {
            drawDirectionShape(
              ctx,
              cand.x,
              cand.tipY - dirSign * 2.5,
              cand.baseY + dirSign * 2,
              ARROW_HALF_W + 2.5,
              cand.cluster.direction,
            );
            ctx.strokeStyle = pal.focusRing;
            ctx.lineWidth = 1.4;
            ctx.stroke();
          }
          ctx.restore();

          // 4. Multi-event count badge when label is folded
          if (!geom.pillRect && cand.cluster.events.length > 1) {
            const badgeText = `${cand.cluster.events.length}`;
            const bx = cand.x + ARROW_HALF_W + 2;
            const by = (cand.tipY + cand.baseY) / 2;
            ctx.save();
            ctx.beginPath();
            ctx.arc(bx + 4, by, 6, 0, Math.PI * 2);
            ctx.fillStyle = pal.pillBg;
            ctx.fill();
            ctx.strokeStyle = col;
            ctx.lineWidth = 1.2;
            ctx.stroke();
            ctx.fillStyle = pal.pillText;
            ctx.font = '700 9px -apple-system, BlinkMacSystemFont, sans-serif';
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            ctx.fillText(badgeText, bx + 4, by + 0.5);
            ctx.restore();
          }

          // 5. Opaque pill label with solid (confirmed) or dashed (provisional) border
          if (geom.pillRect) {
            const { x: px, y: py, w: pw, h: ph } = geom.pillRect;
            ctx.save();
            drawRoundedRect(ctx, px, py, pw, ph, 4.5);
            // Outer panel-bg halo so indicator lines never visually merge into border
            ctx.strokeStyle = pal.panel;
            ctx.lineWidth = 2.5;
            ctx.stroke();
            ctx.fillStyle = pal.pillBg;
            ctx.fill();

            if (cand.cluster.provisional) {
              ctx.setLineDash([3.5, 2.5]);
            }
            ctx.strokeStyle = cand.selected ? pal.focusRing : col;
            ctx.lineWidth = cand.selected ? 1.6 : 1.2;
            ctx.stroke();
            ctx.setLineDash([]);

            const glyph = directionGlyph(cand.cluster.direction, cand.cluster.provisional);
            ctx.font = FONT_SPEC;
            ctx.textBaseline = "middle";
            ctx.textAlign = "left";
            ctx.fillStyle = col;
            ctx.fillText(glyph, px + 5.5, py + ph / 2 + 0.5);
            const glyphW = ctx.measureText(`${glyph} `).width;
            ctx.fillStyle = pal.pillText;
            ctx.fillText(cand.labelText, px + 5.5 + glyphW, py + ph / 2 + 0.5);
            ctx.restore();
          }
        }

        ctx.restore();
      }

      // 6. Decision badges (kept distinct from strategy arrows with square rounded tags)
      if (this.showDecisions && this.decisions.length > 0) {
        const eventBelowByBar = new Set(
          rendered.filter((r) => r.side === "below").map((r) => r.barTime),
        );
        ctx.save();
        ctx.font = '700 9.5px -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif';
        ctx.textAlign = "center";
        ctx.textBaseline = "middle";
        const seenDecBars = new Map<string, number>();
        for (const d of this.decisions) {
          const bar = this.barMap.get(d.barTime);
          if (!bar) continue;
          const x = chart.timeScale().timeToCoordinate(d.barTime as Time);
          if (x === null || x < -16 || x > width + 16) continue;
          const lowY = series.priceToCoordinate(bar.low);
          if (lowY === null) continue;
          const stack = seenDecBars.get(d.barTime) ?? 0;
          seenDecBars.set(d.barTime, stack + 1);
          const extraOffset = eventBelowByBar.has(d.barTime) ? 46 : 12;
          const cy = Math.min(height - 10, lowY + extraOffset + stack * 18);
          const col =
            d.action === "买入"
              ? pal.up
              : d.action === "卖出"
                ? pal.down
                : d.action === "持有"
                  ? pal.accent
                  : pal.blue;
          const size = 15;
          drawRoundedRect(ctx, x - size / 2, cy - size / 2, size, size, 3.5);
          ctx.strokeStyle = pal.panel;
          ctx.lineWidth = 2.5;
          ctx.stroke();
          ctx.fillStyle = col;
          ctx.fill();
          ctx.fillStyle = "#ffffff";
          ctx.fillText(d.shortText, x, cy + 0.5);
        }
        ctx.restore();
      }

      const next: MarkerLayoutSummary = {
        visibleCount: rendered.length,
        foldedCount,
        rendered,
      };
      this.lastLayout = next;
      this.onLayoutChange?.(next);
    });
  }
}
