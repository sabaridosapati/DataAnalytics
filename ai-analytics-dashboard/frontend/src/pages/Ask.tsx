import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { ApiError, api } from "../api/client";
import type { AnswerData, ChatDetail, ChatList, ChatTurnRecord, QueryResponse } from "../api/types";
import AnswerCard from "../components/AnswerCard";
import ChatSidebar from "../components/ChatSidebar";

const EXAMPLES = [
  "What is the total revenue by region?",
  "Show the monthly revenue trend",
  "Why did Q3 performance drop?",
  "What is the average salary by department?",
  "Show the monthly revenue trend and explain the Q3 dip",
];

const CHATS_PAGE = 50;

const FAILURE_MESSAGES: Record<string, string> = {
  plan_failed: "I couldn't turn this question into a valid analysis plan. Try rephrasing it.",
  llm_quota_exhausted: "The AI provider's quota is exhausted. Ask the administrator to check API billing and limits.",
  llm_unavailable: "The language model was unavailable for this question.",
  timeout: "This question took too long to answer.",
};

interface Turn {
  id: number;
  question: string;
  response?: AnswerData;
  error?: string;
  loading: boolean;
}

function fromRecord(t: ChatTurnRecord): Turn {
  return {
    id: t.id,
    question: t.question,
    response: t.response ?? undefined,
    error: t.response ? undefined : FAILURE_MESSAGES[t.status] ?? "This question could not be answered.",
    loading: false,
  };
}

export default function Ask() {
  const queryClient = useQueryClient();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [chatId, setChatId] = useState<number | null>(null);
  const [question, setQuestion] = useState("");
  const bottom = useRef<HTMLDivElement>(null);
  const [opening, setOpening] = useState(false);
  const [openError, setOpenError] = useState<string | null>(null);
  const busy = opening || turns.some((t) => t.loading);

  const chats = useInfiniteQuery({
    queryKey: ["chats"],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => api<ChatList>(`/chats?limit=${CHATS_PAGE}&offset=${pageParam}`),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((n, p) => n + p.items.length, 0);
      return last.items.length > 0 && loaded < last.total ? loaded : undefined;
    },
  });
  const chatItems = chats.data?.pages.flatMap((p) => p.items) ?? [];

  useEffect(() => {
    bottom.current?.scrollIntoView?.({ behavior: "smooth" });
  }, [turns]);

  function newConversation() {
    setOpenError(null);
    setChatId(null);
    setTurns([]);
  }

  async function openChat(id: number) {
    if (busy) return;
    setOpening(true);
    setOpenError(null);
    try {
      const detail = await api<ChatDetail>(`/chats/${id}`);
      setChatId(detail.id);
      setTurns(detail.turns.map(fromRecord));
    } catch {
      setOpenError("Could not open this conversation. It may have been deleted.");
      void queryClient.invalidateQueries({ queryKey: ["chats"] });
    } finally {
      setOpening(false);
    }
  }

  async function deleteChat(id: number) {
    if (busy) return;
    try {
      await api(`/chats/${id}`, { method: "DELETE" });
      if (id === chatId) newConversation();
    } catch {
      // keep the page as it is; the refreshed list below drops rows that no longer exist
    } finally {
      void queryClient.invalidateQueries({ queryKey: ["chats"] });
    }
  }

  async function ask(text: string) {
    const q = text.trim();
    if (!q || busy) return;
    const id = Date.now();
    setTurns((ts) => [...ts, { id, question: q, loading: true }]);
    setQuestion("");
    try {
      const response = await api<QueryResponse>("/query", { body: { question: q, ...(chatId !== null && { chat_id: chatId }) } });
      setChatId(response.chat_id);
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, response, loading: false } : t)));
    } catch (err) {
      if (err instanceof ApiError && err.status === 404 && err.code === "not_found" && chatId !== null) setChatId(null);
      else if (err instanceof ApiError && err.chatId !== undefined) setChatId(err.chatId);
      setTurns((ts) => ts.map((t) => (t.id === id ? { ...t, error: err instanceof Error ? err.message : "Request failed", loading: false } : t)));
    } finally {
      void queryClient.invalidateQueries({ queryKey: ["chats"] });
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    void ask(question);
  }

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-6 md:flex-row">
      <ChatSidebar
        chats={chatItems}
        total={chats.data?.pages[chats.data.pages.length - 1]?.total}
        onLoadMore={() => void chats.fetchNextPage()}
        loadingMore={chats.isFetchingNextPage}
        error={chats.isError}
        activeId={chatId}
        busy={busy}
        onSelect={(id) => void openChat(id)}
        onDelete={(id) => void deleteChat(id)}
        onNew={newConversation}
      />
      <div className="min-w-0 flex-1">
        <div className="mb-6">
          <h1 className="text-2xl font-semibold">Ask your data</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">Questions are routed to SQL, document, compute and chart agents. Every number is checked against the data.</p>
        </div>

        {turns.length === 0 && (
          <div className="card mb-6">
            <p className="mb-3 text-sm font-medium">Try one of these:</p>
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((e) => (
                <button key={e} type="button" className="btn-secondary text-left" disabled={busy} onClick={() => void ask(e)}>{e}</button>
              ))}
            </div>
          </div>
        )}

        {openError && <p className="error-text mb-4" role="alert">{openError}</p>}

        <div className="space-y-6">
          {turns.map((t) => (
            <div key={t.id} className="space-y-3">
              <div className="flex justify-end">
                <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-indigo-600 px-4 py-2 text-white">{t.question}</div>
              </div>
              <div className="card">
                {t.loading && <p className="animate-pulse text-sm text-slate-500">Analyzing… planning steps, querying data and checking numbers.</p>}
                {t.error && <p className="error-text" role="alert">{t.error}</p>}
                {t.response && <AnswerCard response={t.response} />}
              </div>
            </div>
          ))}
          <div ref={bottom} />
        </div>

        <form onSubmit={onSubmit} className="sticky bottom-0 mt-6 flex gap-2 bg-slate-50 py-4 dark:bg-slate-950">
          <label htmlFor="question" className="sr-only">Question</label>
          <input
            id="question"
            className="input"
            placeholder="e.g. Which product category grew fastest in Q4?"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            maxLength={2000}
          />
          <button type="submit" className="btn shrink-0" disabled={busy || !question.trim()}>Ask</button>
        </form>
      </div>
    </div>
  );
}
