import type {
  AssetChartPayload,
  BootstrapPayload,
  CatalogItem,
  ChartTemplate,
  CheckRunSummary,
  DecisionRecord,
  NoteRecord,
  SignalEventRecord,
  StrategyRule,
} from "./types";

let csrfToken = "";

export function setCsrfToken(token: string) {
  csrfToken = token;
}

async function request<T>(url: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (csrfToken && init.method && init.method !== "GET") {
    headers.set("X-CSRF-Token", csrfToken);
  }
  const resp = await fetch(url, { ...init, headers });
  const text = await resp.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!resp.ok) {
    const detail =
      typeof data === "object" && data !== null && "detail" in data
        ? (data as { detail: unknown }).detail
        : data;
    const msg =
      typeof detail === "string"
        ? detail
        : typeof detail === "object" && detail !== null && "message" in detail
          ? String((detail as { message: unknown }).message)
          : `请求失败 (${resp.status})`;
    throw new Error(msg);
  }
  return data as T;
}

export const api = {
  bootstrap: async () => {
    const res = await request<BootstrapPayload>("/api/v1/bootstrap");
    setCsrfToken(res.csrf);
    return res;
  },
  catalog: (q = "") =>
    request<{ assets: CatalogItem[] }>(`/api/v1/catalog?q=${encodeURIComponent(q)}`),
  addAsset: (version: number, asset: Record<string, unknown>) =>
    request<{ version: number; selected: string }>("/api/v1/assets", {
      method: "POST",
      body: JSON.stringify({ version, asset }),
    }),
  updateAsset: (assetId: string, payload: Record<string, unknown>) =>
    request<{ version: number; asset_id: string }>(`/api/v1/assets/${encodeURIComponent(assetId)}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  reorderAssets: (version: number, ids: string[]) =>
    request<{ version: number }>("/api/v1/assets/order", {
      method: "PUT",
      body: JSON.stringify({ version, ids }),
    }),
  assetBars: (assetId: string) =>
    request<AssetChartPayload>(`/api/v1/assets/${encodeURIComponent(assetId)}/bars`),
  refreshAssetQuote: (assetId: string) =>
    request<AssetChartPayload>(`/api/v1/assets/${encodeURIComponent(assetId)}/quote`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  previewAsset: (assetId: string, strategies: StrategyRule[]) =>
    request<AssetChartPayload>(`/api/v1/assets/${encodeURIComponent(assetId)}/preview`, {
      method: "POST",
      body: JSON.stringify({ strategies }),
    }),
  updateMonitoring: (assetId: string, payload: Record<string, unknown>) =>
    request<{ version: number; strategy_id: string | null }>(
      `/api/v1/assets/${encodeURIComponent(assetId)}/monitoring`,
      {
        method: "PUT",
        body: JSON.stringify(payload),
      },
    ),
  addDecision: (
    assetId: string,
    payload: {
      action: DecisionRecord["action"];
      execution: DecisionRecord["execution"];
      note: string;
      bar_date?: string;
      price_at_decision?: number | null;
      exploration_strategies?: StrategyRule[];
    },
  ) =>
    request<{ id: number; decisions: DecisionRecord[] }>(
      `/api/v1/assets/${encodeURIComponent(assetId)}/decisions`,
      {
        method: "POST",
        body: JSON.stringify(payload),
      },
    ),
  signals: (params?: { asset_id?: string; limit?: number }) => {
    const qs = new URLSearchParams();
    if (params?.asset_id) qs.set("asset_id", params.asset_id);
    if (params?.limit) qs.set("limit", String(params.limit));
    const suffix = qs.toString() ? `?${qs.toString()}` : "";
    return request<{ total?: number; signals: SignalEventRecord[] }>(`/api/v1/signals${suffix}`);
  },
  refreshSummaries: (assetIds?: string[]) =>
    request<{ refreshed_assets: number; summary_rows: number; refreshed_at: string }>(
      "/api/v1/summaries/refresh",
      {
        method: "POST",
        body: JSON.stringify(assetIds ? { asset_ids: assetIds } : {}),
      },
    ),
  runs: (limit = 30) =>
    request<{ running: boolean; runs: CheckRunSummary[] }>(`/api/v1/runs?limit=${limit}`),
  triggerRun: (asyncMode = false) =>
    request<{ run_id?: number; started?: boolean; running?: boolean }>("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify({ async: asyncMode }),
    }),
  strategies: () =>
    request<{ version: number; strategies: StrategyRule[] }>("/api/v1/strategies"),
  saveStrategy: (payload: Record<string, unknown>) =>
    request<{ version: number; strategy_id: string | null }>("/api/v1/strategies", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  saveTemplate: (payload: {
    name: string;
    strategies: StrategyRule[];
    template_id?: string;
    expected?: number;
  }) =>
    request<{ templates: ChartTemplate[] }>("/api/v1/templates", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  notes: (assetId?: string) =>
    request<{ notes: NoteRecord[] }>(
      assetId ? `/api/v1/notes?asset_id=${encodeURIComponent(assetId)}` : "/api/v1/notes",
    ),
  createNote: (payload: { title?: string; markdown: string; asset_ids?: string[] }) =>
    request<NoteRecord>("/api/v1/notes", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  updateNote: (
    noteId: number,
    payload: { title?: string; markdown?: string; asset_ids?: string[]; updated_at?: string },
  ) =>
    request<NoteRecord>(`/api/v1/notes/${noteId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  pinNote: (noteId: number) =>
    request<NoteRecord>(`/api/v1/notes/${noteId}/pin`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  deleteNote: (noteId: number, updatedAt?: string) =>
    request<{ deleted: boolean; id: number }>(
      updatedAt
        ? `/api/v1/notes/${noteId}?updated_at=${encodeURIComponent(updatedAt)}`
        : `/api/v1/notes/${noteId}`,
      {
        method: "DELETE",
      },
    ),
};
