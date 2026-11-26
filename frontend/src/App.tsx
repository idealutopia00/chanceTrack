import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import {
  Activity,
  Bell,
  BookOpen,
  Moon,
  PanelLeft,
  PanelRight,
  Pin,
  RefreshCw,
  Sliders,
  Sun,
  X,
} from "lucide-react";
import { api } from "./api";
import { AddAssetModal } from "./components/AddAssetModal";
import { PinnedNoteModal } from "./components/PinnedNoteModal";
import type { BootstrapPayload, ThemePref, ViewKey } from "./types";
import { NotesView } from "./views/NotesView";
import { SignalsView } from "./views/SignalsView";
import { StrategiesView } from "./views/StrategiesView";
import { WorkspaceView } from "./views/WorkspaceView";

function viewFromPath(pathname: string): ViewKey {
  if (pathname.startsWith("/signals")) return "signals";
  if (pathname.startsWith("/strategies") || pathname.startsWith("/manage")) return "strategies";
  if (pathname.startsWith("/notes") || pathname.startsWith("/investment-notes")) return "notes";
  return "workspace";
}

function pathForView(view: ViewKey): string {
  switch (view) {
    case "signals":
      return "/signals";
    case "strategies":
      return "/strategies";
    case "notes":
      return "/notes";
    default:
      return "/";
  }
}

export function App() {
  const [bootstrap, setBootstrap] = useState<BootstrapPayload | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [view, setView] = useState<ViewKey>(() => viewFromPath(window.location.pathname));

  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(() => {
    try {
      return localStorage.getItem("ct_selected_asset");
    } catch {
      return null;
    }
  });
  const [focusDate, setFocusDate] = useState<string | null>(null);
  const [noteToOpen, setNoteToOpen] = useState<number | null>(null);

  const [watchlistOpen, setWatchlistOpen] = useState(() => window.innerWidth >= 960);
  const [drawerOpen, setDrawerOpen] = useState(() => window.innerWidth >= 1180);
  const lastWidthRef = useRef(window.innerWidth);

  useEffect(() => {
    const onResize = () => {
      const nextWidth = window.innerWidth;
      if (nextWidth === lastWidthRef.current) return;
      const prevWidth = lastWidthRef.current;
      lastWidthRef.current = nextWidth;
      if (prevWidth >= 960 && nextWidth < 960) setWatchlistOpen(false);
      if (prevWidth >= 1180 && nextWidth < 1180) setDrawerOpen(false);
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  const [addModalOpen, setAddModalOpen] = useState(false);
  const [pinnedModalOpen, setPinnedModalOpen] = useState(false);
  const [checking, setChecking] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const [themePref, setThemePref] = useState<ThemePref>(() => {
    try {
      const saved = localStorage.getItem("ct_theme");
      if (saved === "dark" || saved === "light" || saved === "system") return saved;
    } catch {}
    return "system";
  });

  const [resolvedTheme, setResolvedTheme] = useState<"dark" | "light">(() =>
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );

  // Sync theme to documentElement synchronously before paint
  useLayoutEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const apply = () => {
      const resolved =
        themePref === "dark" || (themePref === "system" && mq.matches) ? "dark" : "light";
      document.documentElement.dataset.theme = resolved;
      setResolvedTheme(resolved);
    };
    apply();
    try {
      localStorage.setItem("ct_theme", themePref);
    } catch {}
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [themePref]);

  const refreshBootstrap = useCallback(async () => {
    try {
      const res = await api.bootstrap();
      setBootstrap(res);
      setLoadError(null);
      setSelectedAssetId((prev) => {
        if (prev && res.assets.some((a) => a.id === prev && !a.archived)) return prev;
        return res.assets.find((a) => !a.archived && a.watchlisted)?.id ?? res.assets[0]?.id ?? null;
      });
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    refreshBootstrap();
  }, [refreshBootstrap]);

  // Persist selected asset across reloads
  useEffect(() => {
    if (selectedAssetId) {
      try {
        localStorage.setItem("ct_selected_asset", selectedAssetId);
      } catch {}
    }
  }, [selectedAssetId]);

  // SPA browser history sync
  useEffect(() => {
    const onPop = () => setView(viewFromPath(window.location.pathname));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  function navigate(next: ViewKey) {
    setView(next);
    const targetPath = pathForView(next);
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, "", targetPath);
    }
  }

  // Global keyboard shortcuts: / search, [ toggle watchlist, ] toggle drawer
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (document.querySelector('[role="dialog"]')) return;
      const target = e.target as HTMLElement | null;
      const tag = target?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || target?.isContentEditable) {
        return;
      }
      if (e.key === "/") {
        e.preventDefault();
        setAddModalOpen(true);
      } else if (e.key === "[") {
        setWatchlistOpen((v) => !v);
      } else if (e.key === "]") {
        setDrawerOpen((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  async function handleRunCheck() {
    setChecking(true);
    setActionError(null);
    try {
      await api.triggerRun(false);
      await refreshBootstrap();
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(false);
    }
  }

  function cycleTheme() {
    setThemePref(resolvedTheme === "dark" ? "light" : "dark");
  }

  const signalCount =
    bootstrap?.assets.filter((a) => !a.archived && a.latest_signal).length ?? 0;

  return (
    <div className="h-dvh w-full flex flex-col overflow-hidden" style={{ background: "var(--app-bg)" }}>
      {/* TOP GLOBAL BAR (44px) */}
      <header
        className="h-11 shrink-0 px-2 sm:px-3 border-b flex items-center justify-between gap-1.5 sm:gap-2"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
      >
        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 shrink-0">
          <button
            type="button"
            onClick={() => navigate("workspace")}
            className="inline-flex items-center gap-2 font-bold text-sm tracking-tight cursor-pointer shrink-0"
          >
            <span
              className="w-6 h-6 rounded-md inline-flex items-center justify-center text-xs font-extrabold tracking-tighter"
              style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
            >
              ct
            </span>
            <span className="hidden sm:inline">Chance Track</span>
          </button>

          {view === "workspace" && (
            <div className="flex items-center gap-1 ml-0.5 sm:ml-1 shrink-0">
              <button
                type="button"
                onClick={() => setWatchlistOpen((v) => !v)}
                title="展开/折叠自选列表 ([)"
                className="h-7 px-2 rounded-md border text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 whitespace-nowrap"
                style={{
                  background: watchlistOpen ? "var(--app-elev)" : "transparent",
                  borderColor: "var(--app-border)",
                  color: "var(--app-muted)",
                }}
              >
                <PanelLeft className="w-3.5 h-3.5" />
                <span className="hidden md:inline">自选</span>
              </button>
              <button
                type="button"
                onClick={() => setDrawerOpen((v) => !v)}
                title="展开/折叠上下文面板 (])"
                className="h-7 px-2 rounded-md border text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 whitespace-nowrap"
                style={{
                  background: drawerOpen ? "var(--app-elev)" : "transparent",
                  borderColor: "var(--app-border)",
                  color: "var(--app-muted)",
                }}
              >
                <PanelRight className="w-3.5 h-3.5" />
                <span className="hidden md:inline">面板</span>
              </button>
            </div>
          )}
        </div>

        <div className="flex items-center gap-1.5 sm:gap-2 min-w-0 shrink-0">
          {bootstrap?.pinned_note && (
            <button
              type="button"
              onClick={() => setPinnedModalOpen(true)}
              className="h-7 px-2 sm:px-2.5 rounded-md border text-xs inline-flex items-center gap-1.5 sm:max-w-[200px] md:max-w-[240px] cursor-pointer shrink-0"
              style={{
                background: "var(--app-elev)",
                borderColor: "var(--app-border)",
                color: "var(--app-text)",
              }}
              title={bootstrap.pinned_note.title}
              aria-label={bootstrap.pinned_note.title}
            >
              <Pin className="w-3.5 h-3.5 sm:w-3 sm:h-3 shrink-0" style={{ color: "var(--app-accent)" }} />
              <span className="hidden sm:inline truncate">{bootstrap.pinned_note.title}</span>
            </button>
          )}

          <button
            type="button"
            onClick={cycleTheme}
            title="切换浅/深色主题"
            aria-label="切换主题"
            className="h-7 px-2 rounded-md border text-xs inline-flex items-center gap-1 cursor-pointer shrink-0 whitespace-nowrap"
            style={{
              background: "var(--app-elev)",
              borderColor: "var(--app-border)",
              color: "var(--app-text)",
            }}
          >
            {resolvedTheme === "dark" ? (
              <Sun className="w-3.5 h-3.5" />
            ) : (
              <Moon className="w-3.5 h-3.5" />
            )}
            <span className="hidden sm:inline">
              {resolvedTheme === "dark" ? "浅色" : "深色"}
            </span>
          </button>

          <span
            className="hidden lg:inline-flex items-center gap-1.5 text-[11px] tabular whitespace-nowrap shrink-0"
            style={{ color: "var(--app-muted)" }}
          >
            <span
              className="w-1.5 h-1.5 rounded-full"
              style={{ background: "var(--app-accent)" }}
            />
            工作日 14:35 自动
          </span>

          <button
            type="button"
            disabled={checking}
            onClick={handleRunCheck}
            className="h-7 px-2.5 sm:px-3 rounded-md text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer shrink-0 whitespace-nowrap"
            style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
          >
            <RefreshCw className={`w-3 h-3 shrink-0 ${checking ? "animate-spin" : ""}`} />
            {checking ? (
              "检查中…"
            ) : (
              <span>
                <span className="hidden sm:inline">立即</span>检查
              </span>
            )}
          </button>
        </div>
      </header>

      {actionError && (
        <div
          role="alert"
          className="px-4 py-1.5 text-xs border-b flex items-center justify-between gap-2 shrink-0"
          style={{
            background: "var(--app-warn-soft)",
            color: "var(--app-warn)",
            borderColor: "var(--app-border)",
          }}
        >
          <span>{actionError}</span>
          <button
            type="button"
            onClick={() => setActionError(null)}
            aria-label="关闭错误提示"
            className="p-0.5 rounded cursor-pointer"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      {/* BODY: Left Nav Rail + Active View */}
      <div className="flex-1 min-h-0 flex overflow-hidden">
        <nav
          className="w-14 shrink-0 border-r py-2 flex flex-col items-center gap-1.5"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
          aria-label="主导航"
        >
          {(
            [
              ["workspace", "工作台", Activity, 0],
              ["signals", "信号流", Bell, signalCount],
              ["strategies", "规则库", Sliders, 0],
              ["notes", "备忘录", BookOpen, 0],
            ] as const
          ).map(([key, label, Icon, badge]) => {
            const active = view === key;
            return (
              <button
                key={key}
                type="button"
                onClick={() => navigate(key)}
                aria-current={active ? "page" : undefined}
                className="relative w-12 h-11 rounded-lg flex flex-col items-center justify-center gap-0.5 text-[10px] whitespace-nowrap cursor-pointer"
                style={{
                  background: active ? "var(--app-accent-soft)" : "transparent",
                  color: active ? "var(--app-accent)" : "var(--app-muted)",
                  fontWeight: active ? 650 : 500,
                }}
              >
                <Icon className="w-4 h-4" />
                <span>{label}</span>
                {badge > 0 && (
                  <span
                    className="absolute top-1 right-1 min-w-[14px] h-3.5 px-1 rounded-full text-[9px] font-bold flex items-center justify-center"
                    style={{ background: "var(--app-up)", color: "#fff" }}
                  >
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>

        {loadError && !bootstrap ? (
          <div className="flex-1 flex flex-col items-center justify-center gap-2 text-sm">
            <p style={{ color: "var(--app-warn)" }}>加载失败：{loadError}</p>
            <button
              type="button"
              onClick={refreshBootstrap}
              className="px-3 py-1.5 rounded-md text-xs font-medium cursor-pointer"
              style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
            >
              重试
            </button>
          </div>
        ) : !bootstrap ? (
          <div className="flex-1 flex items-center justify-center text-xs" style={{ color: "var(--app-muted)" }}>
            正在启动 Chance Track 工作台…
          </div>
        ) : (
          <>
            {view === "workspace" && (
              <WorkspaceView
                version={bootstrap.version}
                assets={bootstrap.assets}
                strategies={bootstrap.strategies}
                templates={bootstrap.templates}
                selectedAssetId={selectedAssetId}
                focusDate={focusDate}
                watchlistOpen={watchlistOpen}
                drawerOpen={drawerOpen}
                resolvedTheme={resolvedTheme}
                onSelectAsset={(id) => {
                  setSelectedAssetId(id);
                  setFocusDate(null);
                }}
                onFocusDateChange={setFocusDate}
                onOpenAddAsset={() => setAddModalOpen(true)}
                onOpenNote={(id) => {
                  setNoteToOpen(id ?? null);
                  navigate("notes");
                }}
                onRefreshBootstrap={refreshBootstrap}
              />
            )}
            {view === "signals" && (
              <SignalsView
                onJumpToAsset={(aid, date) => {
                  setSelectedAssetId(aid);
                  setFocusDate(date ?? null);
                  navigate("workspace");
                }}
              />
            )}
            {view === "strategies" && (
              <StrategiesView assets={bootstrap.assets} onConfigChanged={refreshBootstrap} />
            )}
            {view === "notes" && (
              <NotesView
                assets={bootstrap.assets}
                initialNoteId={noteToOpen}
                onNotesChanged={refreshBootstrap}
              />
            )}
          </>
        )}
      </div>

      {/* Modals */}
      {bootstrap && (
        <>
          <AddAssetModal
            open={addModalOpen}
            version={bootstrap.version}
            onClose={() => setAddModalOpen(false)}
            onAdded={async (assetId) => {
              await refreshBootstrap();
              setSelectedAssetId(assetId);
              navigate("workspace");
            }}
          />
          <PinnedNoteModal
            note={bootstrap.pinned_note}
            open={pinnedModalOpen}
            onClose={() => setPinnedModalOpen(false)}
            onOpenNotes={(id) => {
              setNoteToOpen(id ?? null);
              navigate("notes");
            }}
          />
        </>
      )}
    </div>
  );
}
