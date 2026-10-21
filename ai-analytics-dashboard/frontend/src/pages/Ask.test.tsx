import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import Ask from "./Ask";

const answer = (text: string, chat_id: number) => ({
  answer: text, answerable: true, unverified_numbers: [], charts: [], tables: [], sources: [], sql: [],
  plan: null, steps: [], cache_hit: false, request_id: "r", chat_id, turn_id: 1,
});

function mockFetch(handler: (url: string, init?: RequestInit) => unknown) {
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const out = handler(url, init) as { status?: number; body: unknown };
    const status = out.status ?? 200;
    return { ok: status < 400, status, statusText: "", json: async () => out.body };
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderAsk() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={qc}><Ask /></QueryClientProvider>);
}

const bodyOf = (call: unknown[]) => JSON.parse((call[1] as RequestInit).body as string);

describe("Ask page", () => {
  beforeAll(() => { Element.prototype.scrollIntoView = vi.fn(); });
  afterEach(() => vi.unstubAllGlobals());

  it("starts without chat_id, then continues the same chat", async () => {
    const fetchMock = mockFetch((url) => {
      if (url.startsWith("/api/chats?")) return { body: { items: [], total: 0 } };
      if (url === "/api/query") return { body: answer("first answer", 5) };
      return { status: 404, body: {} };
    });
    renderAsk();
    const input = await screen.findByLabelText(/question/i);
    fireEvent.change(input, { target: { value: "first q" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    expect(await screen.findByText("first answer")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "second q" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    await waitFor(() => expect(fetchMock.mock.calls.filter((c) => c[0] === "/api/query")).toHaveLength(2));
    const [first, second] = fetchMock.mock.calls.filter((c) => c[0] === "/api/query").map(bodyOf);
    expect(first.chat_id).toBeUndefined();
    expect(second.chat_id).toBe(5);
    expect(second.history).toBeUndefined();
  });

  it("keeps the conversation when the first question fails", async () => {
    let n = 0;
    const fetchMock = mockFetch((url) => {
      if (url.startsWith("/api/chats?")) return { body: { items: [], total: 0 } };
      n += 1;
      return n === 1
        ? { status: 502, body: { error: { code: "llm_unavailable", message: "The language model is unavailable.", chat_id: 9 } } }
        : { body: answer("recovered", 9) };
    });
    renderAsk();
    const input = await screen.findByLabelText(/question/i);
    fireEvent.change(input, { target: { value: "q1" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    expect(await screen.findByText("The language model is unavailable.")).toBeInTheDocument();
    fireEvent.change(input, { target: { value: "q2" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    expect(await screen.findByText("recovered")).toBeInTheDocument();
    const queries = fetchMock.mock.calls.filter((c) => c[0] === "/api/query").map(bodyOf);
    expect(queries[1].chat_id).toBe(9);
  });

  it("opens a saved chat and renders its turns, including a failed one", async () => {
    mockFetch((url) => {
      if (url.startsWith("/api/chats?")) return { body: { items: [{ id: 3, title: "Old chat", updated_at: "2026-10-16T00:00:00Z", turn_count: 2 }], total: 1 } };
      if (url === "/api/chats/3")
        return {
          body: {
            id: 3, title: "Old chat", created_at: "2026-10-16T00:00:00Z",
            turns: [
              { id: 1, turn_index: 0, question: "saved q", response: answer("saved answer", 3), status: "ok", error: null, created_at: "x" },
              { id: 2, turn_index: 1, question: "failed q", response: null, status: "timeout", error: "exceeded 150s", created_at: "x" },
            ],
          },
        };
      return { status: 404, body: {} };
    });
    renderAsk();
    fireEvent.click(await screen.findByText("Old chat"));
    expect(await screen.findByText("saved answer")).toBeInTheDocument();
    expect(screen.getByText("failed q")).toBeInTheDocument();
    expect(screen.getByText(/took too long/i)).toBeInTheDocument();
    expect(screen.queryByText(/exceeded 150s/)).not.toBeInTheDocument(); // internal error text is not shown
  });

  it("deleting the open chat resets the page", async () => {
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
    const fetchMock = mockFetch((url, init) => {
      if (url.startsWith("/api/chats?")) return { body: { items: [{ id: 3, title: "Old chat", updated_at: "x", turn_count: 1 }], total: 1 } };
      if (url === "/api/chats/3" && init?.method === "DELETE") return { status: 204, body: null };
      if (url === "/api/chats/3")
        return { body: { id: 3, title: "Old chat", created_at: "x", turns: [{ id: 1, turn_index: 0, question: "saved q", response: answer("saved answer", 3), status: "ok", error: null, created_at: "x" }] } };
      return { status: 404, body: {} };
    });
    renderAsk();
    fireEvent.click(await screen.findByText("Old chat"));
    await screen.findByText("saved answer");
    fireEvent.click(screen.getByRole("button", { name: /delete old chat/i }));
    await waitFor(() => expect(screen.queryByText("saved answer")).not.toBeInTheDocument());
    expect(fetchMock.mock.calls.some((c) => c[0] === "/api/chats/3" && (c[1] as RequestInit)?.method === "DELETE")).toBe(true);
  });

  it("loads more chats with the next offset and appends them", async () => {
    const item = (id: number) => ({ id, title: `Chat ${id}`, updated_at: "2026-10-16T00:00:00Z", turn_count: 1 });
    const fetchMock = mockFetch((url) => {
      if (url === "/api/chats?limit=50&offset=0") return { body: { items: [item(1), item(2)], total: 3 } };
      if (url === "/api/chats?limit=50&offset=2") return { body: { items: [item(3)], total: 3 } };
      return { status: 404, body: {} };
    });
    renderAsk();
    await screen.findByText("Chat 2");
    fireEvent.click(screen.getByRole("button", { name: /load more/i }));
    expect(await screen.findByText("Chat 3")).toBeInTheDocument();
    expect(screen.getByText("Chat 1")).toBeInTheDocument();
    expect(fetchMock.mock.calls.some((c) => c[0] === "/api/chats?limit=50&offset=2")).toBe(true);
    expect(screen.queryByRole("button", { name: /load more/i })).not.toBeInTheDocument();
  });

  it("starts a new chat after the open chat turns out to be gone", async () => {
    const fetchMock = mockFetch((url) => {
      if (url.startsWith("/api/chats?")) return { body: { items: [], total: 0 } };
      if (url === "/api/query") {
        const calls = fetchMock.mock.calls.filter((c) => c[0] === "/api/query").length;
        return calls === 1 ? { body: answer("a1", 7) }
          : calls === 2 ? { status: 404, body: { error: { code: "not_found", message: "Chat not found." } } }
          : { body: answer("a3", 8) };
      }
      return { status: 404, body: {} };
    });
    renderAsk();
    const input = await screen.findByLabelText(/question/i);
    const send = async (q: string) => { fireEvent.change(input, { target: { value: q } }); fireEvent.click(screen.getByRole("button", { name: /^ask$/i })); };
    await send("q1");
    await screen.findByText("a1");
    await send("q2");
    await screen.findByText("Chat not found.");
    await send("q3");
    await screen.findByText("a3");
    const q = fetchMock.mock.calls.filter((c) => c[0] === "/api/query").map(bodyOf);
    expect(q[1].chat_id).toBe(7);
    expect(q[2].chat_id).toBeUndefined();
  });

  it("shows an inline error when opening a chat fails", async () => {
    mockFetch((url) => {
      if (url.startsWith("/api/chats?")) return { body: oldList };
      return { status: 500, body: { error: { code: "internal_error", message: "boom" } } };
    });
    renderAsk();
    fireEvent.click(await screen.findByText("Old chat"));
    expect(await screen.findByRole("alert")).toHaveTextContent(/could not open/i);
  });

  it("shows an error in the sidebar when the chat list fails to load", async () => {
    mockFetch(() => ({ status: 500, body: { error: { code: "internal_error", message: "boom" } } }));
    renderAsk();
    expect(await screen.findByText(/could not load conversations/i)).toBeInTheDocument();
    expect(screen.queryByText(/no saved conversations/i)).not.toBeInTheDocument();
  });

  const oldChat = {
    id: 3, title: "Old chat", created_at: "x",
    turns: [{ id: 1, turn_index: 0, question: "saved q", response: answer("saved answer", 3), status: "ok", error: null, created_at: "x" }],
  };
  const oldList = { items: [{ id: 3, title: "Old chat", updated_at: "2026-10-16T00:00:00Z", turn_count: 1 }], total: 1 };

  it("blocks input while a chat loads, ignores a second click, then continues that chat", async () => {
    let release: () => void = () => {};
    const gate = new Promise<void>((r) => { release = r; });
    const fetchMock = vi.fn(async (url: string) => {
      if (url.startsWith("/api/chats?")) return { ok: true, status: 200, statusText: "", json: async () => oldList };
      if (url === "/api/chats/3") {
        await gate;
        return { ok: true, status: 200, statusText: "", json: async () => oldChat };
      }
      if (url === "/api/query") return { ok: true, status: 200, statusText: "", json: async () => answer("follow-up answer", 3) };
      return { ok: false, status: 404, statusText: "", json: async () => ({}) };
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAsk();
    const open = await screen.findByText("Old chat");
    fireEvent.click(open);
    fireEvent.click(open);
    const input = screen.getByLabelText(/question/i);
    fireEvent.change(input, { target: { value: "early q" } });
    expect(screen.getByRole("button", { name: /^ask$/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /new conversation/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /delete old chat/i })).toBeDisabled();
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    release();
    expect(await screen.findByText("saved answer")).toBeInTheDocument();
    expect(fetchMock.mock.calls.filter((c) => c[0] === "/api/chats/3")).toHaveLength(1);
    expect(fetchMock.mock.calls.some((c) => c[0] === "/api/query")).toBe(false);
    fireEvent.change(input, { target: { value: "next q" } });
    fireEvent.click(screen.getByRole("button", { name: /^ask$/i }));
    expect(await screen.findByText("follow-up answer")).toBeInTheDocument();
    const q = fetchMock.mock.calls.filter((c) => c[0] === "/api/query").map((c) => bodyOf(c as unknown[]));
    expect(q).toHaveLength(1);
    expect(q[0].chat_id).toBe(3);
  });

  it("a failed delete keeps the conversation open", async () => {
    vi.stubGlobal("confirm", vi.fn().mockReturnValue(true));
    const fetchMock = mockFetch((url, init) => {
      if (url.startsWith("/api/chats?")) return { body: oldList };
      if (url === "/api/chats/3" && init?.method === "DELETE") return { status: 404, body: { error: { code: "not_found", message: "Chat not found" } } };
      if (url === "/api/chats/3") return { body: oldChat };
      return { status: 404, body: {} };
    });
    renderAsk();
    fireEvent.click(await screen.findByText("Old chat"));
    await screen.findByText("saved answer");
    fireEvent.click(screen.getByRole("button", { name: /delete old chat/i }));
    await waitFor(() => expect(fetchMock.mock.calls.some((c) => (c[1] as RequestInit)?.method === "DELETE")).toBe(true));
    await waitFor(() => expect(fetchMock.mock.calls.filter((c) => (c[0] as string).startsWith("/api/chats?")).length).toBeGreaterThan(1));
    expect(screen.getByText("saved answer")).toBeInTheDocument();
  });
});
