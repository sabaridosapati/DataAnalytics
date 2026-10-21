import type { ChatSummary } from "../api/types";

interface Props {
  chats: ChatSummary[];
  activeId: number | null;
  busy: boolean;
  onSelect: (id: number) => void;
  onDelete: (id: number) => void;
  onNew: () => void;
  total?: number;
  onLoadMore?: () => void;
  loadingMore?: boolean;
  error?: boolean;
}

export default function ChatSidebar({ chats, activeId, busy, onSelect, onDelete, onNew, total, onLoadMore, loadingMore, error }: Props) {
  return (
    <aside className="w-full shrink-0 md:w-64" aria-label="Conversations">
      <button type="button" className="btn mb-3 w-full" onClick={onNew} disabled={busy}>New conversation</button>
      {error && chats.length === 0 ? (
        <p className="error-text" role="alert">Could not load conversations.</p>
      ) : chats.length === 0 ? (
        <p className="text-sm text-slate-500 dark:text-slate-400">No saved conversations yet.</p>
      ) : (
        <ul className="space-y-1">
          {chats.map((c) => (
            <li key={c.id} className="group flex items-center gap-1">
              <button
                type="button"
                disabled={busy}
                onClick={() => onSelect(c.id)}
                aria-current={c.id === activeId ? "true" : undefined}
                className={`min-w-0 flex-1 rounded-lg px-3 py-2 text-left text-sm ${
                  c.id === activeId ? "bg-indigo-600 text-white" : "hover:bg-slate-200 dark:hover:bg-slate-800"
                }`}
                title={c.title}
              >
                <span className="block truncate">{c.title}</span>
                <span className="block text-xs opacity-70">{new Date(c.updated_at).toLocaleDateString()}</span>
              </button>
              <button
                type="button"
                disabled={busy}
                aria-label={`Delete ${c.title}`}
                className="btn-secondary px-2 py-1 text-xs"
                onClick={() => {
                  if (window.confirm(`Delete "${c.title}"? This cannot be undone.`)) onDelete(c.id);
                }}
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}
      {onLoadMore && total !== undefined && chats.length < total && (
        <button type="button" className="btn-secondary mt-2 w-full" onClick={onLoadMore} disabled={busy || loadingMore}>Load more</button>
      )}
    </aside>
  );
}
