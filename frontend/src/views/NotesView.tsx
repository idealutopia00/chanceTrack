import { useEffect, useState } from "react";
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
  const [draftMarkdown, setDraftMarkdown] = useState("");
  const [draftAssetIds, setDraftAssetIds] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const activeAssets = assets.filter((a) => !a.archived);

  async function load(selectId?: number | null) {
    const res = await api.notes();
    setNotes(res.notes);
    if (selectId !== undefined) {
      setSelectedId(selectId);
    } else if (!selectedId && res.notes.length) {
      setSelectedId(res.notes[0].id);
    }
  }

  useEffect(() => {
    load(initialNoteId ?? undefined);
  }, [initialNoteId]);

  const current = notes.find((n) => n.id === selectedId) ?? notes[0] ?? null;

  function startNew() {
    setSelectedId(null);
    setDraftMarkdown("# 新投资备忘\n\n");
    setDraftAssetIds([]);
    setEditing(true);
    setError(null);
  }

  function startEdit(note: NoteRecord) {
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
      const saved = current && selectedId !== null
        ? await api.updateNote(current.id, {
            markdown: draftMarkdown,
            asset_ids: draftAssetIds,
          })
        : await api.createNote({
            markdown: draftMarkdown,
            asset_ids: draftAssetIds,
          });
      setEditing(false);
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
    try {
      await api.pinNote(noteId);
      await load(noteId);
      await onNotesChanged();
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(noteId: number) {
    if (!window.confirm("确定删除这篇备忘录吗？")) return;
    setBusy(true);
    try {
      await api.deleteNote(noteId);
      await load(null);
      await onNotesChanged();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex-1 min-h-0 flex overflow-hidden">
      {/* Left Note List */}
      <aside
        className="w-64 shrink-0 border-r flex flex-col overflow-hidden"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
      >
        <div
          className="px-3 py-2.5 border-b flex items-center justify-between"
          style={{ borderColor: "var(--app-border)" }}
        >
          <span className="text-xs font-bold">投资备忘录 ({notes.length})</span>
          <button
            type="button"
            onClick={startNew}
            className="px-2 py-1 rounded-md text-xs font-medium inline-flex items-center gap-1 cursor-pointer"
            style={{ background: "var(--app-btn)", color: "var(--app-btn-text)" }}
          >
            <Plus className="w-3 h-3" />
            新建
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1">
          {notes.map((n) => {
            const active = current?.id === n.id && selectedId !== null;
            return (
              <button
                key={n.id}
                type="button"
                onClick={() => {
                  setSelectedId(n.id);
                  setEditing(false);
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
                <div className="flex items-center justify-between text-[10.5px]" style={{ color: "var(--app-faint)" }}>
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
          {!notes.length && (
            <div className="text-center py-8 text-xs" style={{ color: "var(--app-muted)" }}>
              暂无备忘录，点击右上角新建。
            </div>
          )}
        </div>
      </aside>

      {/* Right Reading / Editing Canvas */}
      <main className="flex-1 overflow-y-auto p-6">
        <div
          className="max-w-3xl mx-auto rounded-2xl border p-6"
          style={{ background: "var(--app-panel)", borderColor: "var(--app-border)" }}
        >
          {editing ? (
            <form onSubmit={handleSave} className="flex flex-col gap-4">
              <div className="flex items-center justify-between">
                <h2 className="text-base font-bold">
                  {selectedId !== null ? "编辑备忘录" : "新建备忘录"}
                </h2>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setEditing(false)}
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
                  className="p-2.5 rounded-lg text-xs"
                  style={{ background: "var(--app-warn-soft)", color: "var(--app-warn)" }}
                >
                  {error}
                </div>
              )}

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
                    onClick={() => handleDelete(current.id)}
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
