import { useEffect, useRef } from "react";
import { ArrowUpRight, Pin, X } from "lucide-react";
import { MarkdownView } from "./MarkdownView";
import type { NoteRecord } from "../types";

interface PinnedNoteModalProps {
  note: NoteRecord | null;
  open: boolean;
  onClose: () => void;
  onOpenNotes: (noteId?: number) => void;
}

export function PinnedNoteModal({
  note,
  open,
  onClose,
  onOpenNotes,
}: PinnedNoteModalProps) {
  const closeBtnRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const previous = document.activeElement as HTMLElement | null;
    requestAnimationFrame(() => closeBtnRef.current?.focus());
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      previous?.focus?.();
    };
  }, [open, onClose]);

  if (!open || !note) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "rgba(0,0,0,0.55)" }}
      onClick={onClose}
    >
      <div
        className="w-full max-w-3xl max-h-[85vh] flex flex-col rounded-2xl border shadow-2xl overflow-hidden"
        style={{ background: "var(--app-panel)", borderColor: "var(--app-border-strong)" }}
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="pinned-note-modal-title"
      >
        <div
          className="flex items-center justify-between gap-3 px-6 py-4 border-b"
          style={{ borderColor: "var(--app-border)" }}
        >
          <div className="flex items-center gap-2 min-w-0">
            <Pin className="w-4 h-4 shrink-0" style={{ color: "var(--app-accent)" }} />
            <h2
              id="pinned-note-modal-title"
              className="text-lg font-bold tracking-tight truncate"
            >
              {note.title}
            </h2>
          </div>
          <button
            ref={closeBtnRef}
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg cursor-pointer"
            style={{ background: "var(--app-elev)", color: "var(--app-muted)" }}
            aria-label="关闭置顶备忘"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-5">
          <MarkdownView markdown={note.markdown} dialogTitle={note.title} />
        </div>

        <div
          className="flex items-center justify-between px-6 py-3 border-t text-xs"
          style={{ borderColor: "var(--app-border)", background: "var(--app-elev)" }}
        >
          <span className="tabular" style={{ color: "var(--app-muted)" }}>
            更新于 {note.updated_at.slice(0, 16).replace("T", " ")}
          </span>
          <button
            type="button"
            onClick={() => {
              onClose();
              onOpenNotes(note.id);
            }}
            className="inline-flex items-center gap-1 font-medium cursor-pointer"
            style={{ color: "var(--app-accent)" }}
          >
            管理备忘录与历史
            <ArrowUpRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>
    </div>
  );
}
