import { ApiError, api } from "./client";

describe("api error handling", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("exposes chat_id from an error body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        ok: false,
        status: 502,
        statusText: "Bad Gateway",
        json: async () => ({ error: { code: "llm_unavailable", message: "down", chat_id: 7 } }),
      }),
    );
    const err = (await api("/query", { body: { question: "q" } }).catch((e) => e)) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.chatId).toBe(7);
    expect(err.code).toBe("llm_unavailable");
  });

  it("leaves chatId undefined when the error has none", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ ok: false, status: 404, statusText: "x", json: async () => ({ error: { code: "not_found", message: "no" } }) }),
    );
    const err = (await api("/chats/1").catch((e) => e)) as ApiError;
    expect(err.chatId).toBeUndefined();
  });
});
