import { useCallback, useEffect, useMemo, useState } from "react";
import { Edit3, Pin, Plus, Trash2 } from "lucide-react";
import { api } from "../api";
import { MarkdownView } from "../components/MarkdownView";
import type { AssetSummary, NoteRecord } from "../types";

interface NotesViewProps {
  assets: AssetSummary[];
  initialNoteId: number | null;
  onNotesChanged: () => Promise<void>;
}

export function NotesView({ assets, initialNoteId, onNotesChanged }: NotesViewProps) {
  const [notes, setNotes] = useState<NoteRecord[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(initialNoteId);
  const [editing, setEditing] = useState(false);
  const [editingNoteId, setEditingNoteId] = useState<number | null>(null);
  const [editingUpdatedAt, setEditingUpdatedAt] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftMarkdown, setDraftMarkdown] = useState("");
  const [draftAssetIds, setDraftAssetIds] = useState<string[]>([]);
  const [searchQuery, setSearchQuery] = useState("");
  const [assetFilter, setAssetFilter] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeAssets = useMemo(() => assets.filter((a) => !a.archived), [assets]);

  const load = useCallback(async (selectId?: number | null) => {
    try {
      const res = await api.notes();
      setNotes(res.notes);
      setError(null);
      if (selectId !== undefined) {
        setSelectedId(selectId ?? res.notes[0]?.id ?? null);
      } else {
        setSelectedId((prev) => {
          if (prev && res.notes.some((n) => n.id === prev)) return prev;
          return res.notes[0]?.id ?? null;
        });
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    load(initialNoteId ?? undefined);
  }, [initialNoteId, load]);

  const filteredNotes = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return notes.filter((n) => {
      if (assetFilter && !(n.asset_ids || []).includes(assetFilter)) return false;
      if (q) {
        const hay = `${n.title} ${n.markdown}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [notes, searchQuery, assetFilter]);

  const current =
    filteredNotes.find((n) => n.id === selectedId) ??
    notes.find((n) => n.id === selectedId) ??
    filteredNotes[0] ??
    null;

  function startNew() {
    setEditingNoteId(null);
    setEditingUpdatedAt(null);
    setDraftTitle("");
    setDraftMarkdown("# 新投资备忘\n\n");
    setDraftAssetIds(assetFilter ? [assetFilter] : []);
    setEditing(true);
    setError(null);
  }

  function startEdit(note: NoteRecord) {
    setSelectedId(note.id);
    setEditingNoteId(note.id);
    setEditingUpdatedAt(note.updated_at);
    setDraftTitle(note.title);
    setDraftMarkdown(note.markdown);
    setDraftAssetIds(note.asset_ids || []);
    setEditing(true);
    setError(null);
  }

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const saved =
        editingNoteId !== null
          ? await api.updateNote(editingNoteId, {
              title: draftTitle.trim() || undefined,
              markdown: draftMarkdown,
              asset_ids: draftAssetIds,
              updated_at: editingUpdatedAt ?? undefined,
            })
          : await api.createNote({
              title: draftTitle.trim() || undefined,
              markdown: draftMarkdown,
              asset_ids: draftAssetIds,
            });
      setEditing(false);
      setEditingNoteId(null);
      setEditingUpdatedAt(null);
      await load(saved.id);
      await onNotesChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handlePin(noteId: number) {
    setBusy(true);
    setError(null);
    try {
      await api.pinNote(noteId);
      await load(noteId);
      await onNotesChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(note: NoteRecord) {
    if (!window.confirm("确定删除这篇备忘录吗？")) return;
    setBusy(true);
    setError(null);
    try {
      await api.deleteNote(note.id, note.updated_at);
      setEditing(false);
      setEditingNoteId(null);
      await load(null);
      await onNotesChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex-1 min-h-0 flex flex-col md:flex-row overflow-hidden">
      {/* Left Note List (stacks cleanly on narrow <768px screens) */}
      <aside
        className="w-full md:w-64 shrink-0 border-b md:border-b-0 md:border-r flex flex-col max-h-52 md:max-h-none overflow-hidden"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
      >
        <div
          className="px-3 py-2.5 border-b flex items-center justify-between gap-2"
          style={{ borderColor: "var(--app-border)" }}
        >
          <span className="text-xs font-bold">投资备忘录 ({notes.length})</span>
          <button
            type="button"
            onClick={startNew}
            className="px-2 py-1 rounded-md text-xs font-medium inline-flex items-center gap-1 cursor-pointer shrink-0"
            style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
          >
            <Plus className="w-3 h-3" />
            新建
          </button>
        </div>

        <div
          className="px-2.5 py-2 border-b flex items-center gap-1.5"
          style={{ borderColor: "var(--app-border)" }}
        >
          <input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="检索备忘…"
            className="flex-1 min-w-0 h-7 px-2 rounded-md border text-xs bg-transparent outline-none"
            style={{ background: "var(--app-bg)", borderColor: "var(--app-border)" }}
          />
          <select
            value={assetFilter}
            onChange={(e) => setAssetFilter(e.target.value)}
            aria-label="按标的筛选备忘"
            className="h-7 max-w-[96px] px-1.5 rounded-md border text-[11px]"
            style={{ background: "var(--app-bg)", borderColor: "var(--app-border)" }}
          >
            <option value="">全部标的</option>
            {activeAssets.map((a) => (
              <option key={a.id} value={a.id}>
                {a.label}
              </option>
            ))}
          </select>
        </div>

        <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1">
          {filteredNotes.map((n) => {
            const active = current?.id === n.id && !editing;
            return (
              <button
                key={n.id}
                type="button"
                onClick={() => {
                  setSelectedId(n.id);
                  setEditing(false);
                  setEditingNoteId(null);
                  setError(null);
                }}
                className="text-left p-2.5 rounded-lg border flex flex-col gap-1 cursor-pointer"
                style={{
                  background: active ? "var(--app-elev)" : "transparent",
                  borderColor: active ? "var(--app-border-strong)" : "transparent",
                }}
              >
                <div className="flex items-center gap-1.5">
                  {n.pinned && (
                    <Pin className="w-3 h-3 shrink-0" style={{ color: "var(--app-accent)" }} />
                  )}
                  <span className="text-xs font-semibold truncate">{n.title}</span>
                </div>
                <div
                  className="flex items-center justify-between text-[10.5px]"
                  style={{ color: "var(--app-faint)" }}
                >
                  <span className="truncate">
                    {n.assets?.length
                      ? `关联：${n.assets.map((a) => a.label).join("、")}`
                      : n.pinned
                        ? "全局置顶"
                        : "通用备忘"}
                  </span>
                  <span className="font-mono tabular shrink-0">{n.updated_at.slice(5, 10)}</span>
                </div>
              </button>
            );
          })}
          {!filteredNotes.length && (
            <div className="text-center py-6 text-xs" style={{ color: "var(--app-muted)" }}>
              {notes.length ? "无匹配备忘录" : "暂无备忘录，点击右上角新建。"}
            </div>
          )}
        </div>
      </aside>

      {/* Right Reading / Editing Canvas */}
      <main className="flex-1 min-w-0 overflow-y-auto p-4 sm:p-6">
        <div
          className="max-w-3xl mx-auto rounded-2xl border p-4 sm:p-6"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          {error && !editing && (
            <div
              role="alert"
              className="mb-4 p-2.5 rounded-lg text-xs"
              style={{ background: "var(--app-warn-soft)", color: "var(--app-warn)" }}
            >
              {error}
            </div>
          )}

          {editing ? (
            <form onSubmit={handleSave} className="flex flex-col gap-4">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <h2 className="text-base font-bold">
                  {editingNoteId !== null ? "编辑备忘录" : "新建备忘录"}
                </h2>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => {
                      setEditing(false);
                      setEditingNoteId(null);
                      setError(null);
                    }}
                    className="px-3 py-1.5 rounded-lg border text-xs cursor-pointer"
                    style={{ borderColor: "var(--app-border)", color: "var(--app-muted)" }}
                  >
                    取消
                  </button>
                  <button
                    type="submit"
                    disabled={busy}
                    className="px-4 py-1.5 rounded-lg text-xs font-semibold cursor-pointer"
                    style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
                  >
                    {busy ? "保存中…" : "保存备忘"}
                  </button>
                </div>
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
                <span style={{ color: "var(--app-muted)" }}>标题（留空则沿用首行标题）</span>
                <input
                  value={draftTitle}
                  onChange={(e) => setDraftTitle(e.target.value)}
                  placeholder="可选自定义标题…"
                  className="px-3 py-2 rounded-lg border bg-transparent text-sm outline-none"
                  style={{ borderColor: "var(--app-border-strong)" }}
                />
              </label>

              <div>
                <div className="text-xs mb-1.5" style={{ color: "var(--app-muted)" }}>
                  可选关联标的（在工作台切换至对应标的时自动浮现）：
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {activeAssets.map((a) => {
                    const checked = draftAssetIds.includes(a.id);
                    return (
                      <button
                        key={a.id}
                        type="button"
                        onClick={() =>
                          setDraftAssetIds((prev) =>
                            checked ? prev.filter((id) => id !== a.id) : [...prev, a.id],
                          )
                        }
                        className="px-2.5 py-1 rounded-full border text-xs cursor-pointer"
                        style={{
                          background: checked ? "var(--app-blue-soft)" : "var(--app-bg)",
                          borderColor: checked ? "var(--app-blue)" : "var(--app-border)",
                          color: checked ? "var(--app-blue)" : "var(--app-muted)",
                        }}
                      >
                        {checked ? "✓ " : "+ "}
                        {a.label}
                      </button>
                    );
                  })}
                </div>
              </div>

              <textarea
                required
                rows={16}
                value={draftMarkdown}
                onChange={(e) => setDraftMarkdown(e.target.value)}
                placeholder="# 标题\n\n支持标准 Markdown 与多列表格…"
                className="w-full p-3.5 rounded-xl border font-mono text-sm leading-relaxed outline-none"
                style={{
                  background: "var(--app-bg)",
                  borderColor: "var(--app-border-strong)",
                }}
              />
            </form>
          ) : current ? (
            <>
              <div
                className="flex items-center justify-between gap-3 pb-4 mb-5 border-b flex-wrap"
                style={{ borderColor: "var(--app-border)" }}
              >
                <div className="flex items-center gap-2 flex-wrap">
                  {current.pinned && (
                    <span
                      className="px-2 py-0.5 rounded-full text-xs font-medium inline-flex items-center gap-1"
                      style={{
                        background: "var(--app-accent-soft)",
                        color: "var(--app-accent)",
                      }}
                    >
                      <Pin className="w-3 h-3" />
                      全局置顶
                    </span>
                  )}
                  {current.assets?.map((a) => (
                    <span
                      key={a.id}
                      className="px-2 py-0.5 rounded-full text-xs"
                      style={{ background: "var(--app-blue-soft)", color: "var(--app-blue)" }}
                    >
                      {a.label}
                    </span>
                  ))}
                  <span className="text-xs font-mono tabular" style={{ color: "var(--app-faint)" }}>
                    更新于 {current.updated_at.slice(0, 16).replace("T", " ")}
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => startEdit(current)}
                    className="px-2.5 py-1 rounded-md border text-xs inline-flex items-center gap-1 cursor-pointer"
                    style={{
                      background: "var(--app-elev)",
                      borderColor: "var(--app-border)",
                    }}
                  >
                    <Edit3 className="w-3.5 h-3.5" />
                    编辑
                  </button>
                  {!current.pinned && (
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => handlePin(current.id)}
                      className="px-2.5 py-1 rounded-md border text-xs inline-flex items-center gap-1 cursor-pointer"
                      style={{
                        background: "var(--app-elev)",
                        borderColor: "var(--app-border)",
                      }}
                    >
                      <Pin className="w-3.5 h-3.5" />
                      置顶
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleDelete(current)}
                    className="px-2.5 py-1 rounded-md border text-xs inline-flex items-center gap-1 cursor-pointer"
                    style={{ borderColor: "var(--app-border)", color: "var(--app-up)" }}
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                    删除
                  </button>
                </div>
              </div>

              <MarkdownView markdown={current.markdown} />
            </>
          ) : (
            <div className="py-16 text-center text-sm" style={{ color: "var(--app-muted)" }}>
              还没有投资备忘录，点击左侧「新建」写下你的长期观察纪律。
            </div>
          )}
        </div>
      </main>
    </div>
  );
}
