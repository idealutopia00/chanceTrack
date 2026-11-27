export type ThemePref = "system" | "dark" | "light";
export type ViewKey = "workspace" | "signals" | "strategies" | "notes";
export type SignalDirection = "bullish" | "bearish" | "neutral";

export interface StrategyRule {
  id: string;
  name: string;
  kind: string;
  group?: string;
  group_name?: string;
  revision?: number;
  params?: Record<string, number | string>;
  filter?: string;
  trigger?: string;
  band?: string;
  assets?: { id: string; label: string }[];
}

export interface AssetSummary {
  id: string;
  label: string;
  ticker: string;
  code: string;
  provider: string;
  kind: "index" | "etf" | "spot";
  session_profile: string;
  market: string;
  currency: string;
  group: string;
  group_name: string;
  enabled: boolean;
  archived: boolean;
  watchlisted: boolean;
  priority: boolean;
  aliases: string[];
  strategies: string[];
  activated?: Record<string, string>;
  close: number | null;
  change_pct: number | null;
  bar_date: string | null;
  is_provisional: boolean;
  fetched_at: string | null;
  status: "ok" | "warning" | "error" | "disabled" | "idle";
  error: string | null;
  latest_signal: {
    event_date: string;
    event_kind: string;
    direction?: SignalDirection;
    strategy_name: string;
    is_provisional: boolean;
  } | null;
}

export interface CatalogItem {
  id: string;
  label: string;
  ticker?: string;
  code: string;
  provider: string;
  kind: "index" | "etf" | "spot";
  session_profile: string;
  market?: string;
  currency?: string;
  group?: string;
  group_name?: string;
  aliases?: string[];
  in_workspace?: boolean;
}

export interface CandleBar {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number | null;
}

export interface OverlaySeries {
  id: string;
  name: string;
  kind: string;
  group?: string;
  points: { time: string; value: number }[];
}

export interface MacdPoint {
  time: string;
  dif: number;
  dea: number;
  histogram: number;
}

export interface StrategyResult {
  id: string;
  name: string;
  kind: string;
  group: string;
  version: string;
  state: string;
  bar_date?: string;
  event_date?: string;
  event?: string | null;
  preview_event?: string | null;
  preview_detail?: string | null;
  detail?: string;
  is_provisional?: boolean;
  historical_reference?: boolean;
  new_event?: boolean;
  config?: Record<string, unknown>;
}

export interface ChartEvent {
  date?: string;
  time?: string;
  event: string;
  direction?: "bullish" | "bearish" | "neutral";
  name?: string;
  strategy_id?: string;
  strategy_name?: string;
  kind?: string;
  detail?: string;
  provisional?: boolean;
  price?: number;
  pane?: string;
}

export interface DecisionRecord {
  id: number;
  asset_id: string;
  asset_label?: string;
  asset_code?: string;
  run_id: number | null;
  bar_date: string;
  price_at_decision: number | null;
  action: "买入" | "卖出" | "持有" | "观望";
  execution: "未执行" | "已提交";
  note: string;
  has_exploration: boolean;
  created_at: string;
}

export interface NoteRecord {
  id: number;
  title: string;
  markdown: string;
  pinned: boolean;
  created_at: string;
  updated_at: string;
  asset_ids: string[];
  assets: { id: string; label: string }[];
}

export interface ChartTemplate {
  id: number;
  template_id: string;
  name: string;
  created_at: string;
  strategies: StrategyRule[];
}

export interface FetchSummary {
  code: string;
  label: string;
  reason_label: string;
  snapshot_label: string;
  provider_label: string;
  bar_date: string;
  confirmation: string;
  fetched_at: string | null;
  detail: string;
}

export interface AssetChartPayload {
  asset: AssetSummary;
  has_bars: boolean;
  mode: "formal" | "exploration";
  status: string;
  error: string | null;
  carried_forward?: boolean;
  checked_at?: string | null;
  fetched_at?: string | null;
  provider?: string;
  provider_label?: string;
  change_pct?: number | null;
  fetch_summary: FetchSummary | null;
  result: {
    bar_date: string;
    close: number;
    is_provisional: boolean;
    confirmed_date?: string;
    warnings: string[];
    strategies: StrategyResult[];
    chart: {
      candles: CandleBar[];
      volume_unit?: string;
      overlays: OverlaySeries[];
      macd?: MacdPoint[];
      macd_configs?: { id: string; name: string; points: MacdPoint[] }[];
      strategy_events?: ChartEvent[];
    };
  } | null;
  decisions: DecisionRecord[];
  notes: NoteRecord[];
  strategies_used?: StrategyRule[] | null;
}

export interface SignalEventRecord {
  id: number;
  dedup_key: string;
  asset_id: string;
  asset_label: string;
  asset_code: string;
  asset_ticker: string;
  strategy_id: string | null;
  strategy_name: string;
  strategy_group: string;
  strategy_version: string;
  event_date: string;
  event_kind: string;
  direction?: SignalDirection;
  detail: string;
  is_provisional: boolean;
  historical_reference: boolean;
  first_run_id: number | null;
  notified_at: string | null;
  created_at: string;
}

export interface CheckRunSummary {
  id: number;
  day: string;
  kind: "scheduled" | "manual";
  attempt: number;
  rule_version: string;
  config_version: number;
  code_revision: string;
  late: boolean;
  created_at: string;
  total_assets: number;
  ok_assets: number;
  error_assets: number;
  new_events: number;
  notification_sent_at: string | null;
  notification_error: string | null;
  notification_message: string | null;
}

export interface BootstrapPayload {
  csrf: string;
  version: number;
  assets: AssetSummary[];
  strategies: StrategyRule[];
  templates: ChartTemplate[];
  pinned_note: NoteRecord | null;
  next_regular_check: string | null;
  running: boolean;
  latest_run: {
    id: number;
    day: string;
    kind: string;
    created_at: string;
  } | null;
}
