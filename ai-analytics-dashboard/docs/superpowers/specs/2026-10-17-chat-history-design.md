# Per-user chat history in Postgres — design

Date: 2026-10-17 · Status: draft for review

## Goal

Persist each user's conversations in Postgres so a chat survives page refresh, can be reopened later, and
is tied to the user's account. Today the conversation exists only in browser state (`frontend/src/pages/Ask.tsx`);
the backend only writes a flat per-question row to `app.query_audit`.

**Understanding (confirmed in chat):** `app.users` already lives in Postgres with bcrypt password hashes, so
it is unchanged. What is added is `chats` and `chat_turns`, owned by users.

## Non-goals

- No change to `app.users`, registration, login or password hashing.
- No account-activity audit log (register/login/failed login), usernames at sign-up, last-login tracking or
  password change. These were offered and are deferred.
- No admin access to other users' chats. Admins keep `GET /api/admin/audit` (`query_audit`) as today.
- No chat rename, search, sharing or export.
- No new migration tool; tables are created by the existing `Base.metadata.create_all` at startup.

## Data model (schema `app`)

`chats`
| column | type | notes |
|---|---|---|
| id | integer PK | |
| user_id | FK `app.users.id`, ON DELETE CASCADE, indexed | owner |
| title | varchar(120) | first question, whitespace-collapsed, truncated to 120 |
| created_at | timestamptz, default now() | |
| updated_at | timestamptz, default now() | bumped when a turn is added; list sorts by it desc |

`chat_turns`
| column | type | notes |
|---|---|---|
| id | integer PK | |
| chat_id | FK `app.chats.id`, ON DELETE CASCADE, indexed | |
| turn_index | integer | 0-based; `UNIQUE (chat_id, turn_index)` |
| question | text | normalised question (as audited today) |
| response | JSONB, nullable | the full `answer_question` result (answer, steps, sql, charts, tables, flags). Null when the request failed |
| status | varchar(32) | same values as `query_audit.status` (ok, partial, unanswerable, plan_failed, llm_unavailable, timeout, …) |
| error | text, nullable | internal error text, as in `query_audit` |
| latency_ms | integer | |
| cache_hit | boolean | |
| created_at | timestamptz, default now() | |

Storing the whole response lets a reopened chat render through the existing `AnswerCard` unchanged.
Responses can be large (SQL results are capped at `max_sql_rows` = 5000); this is accepted. `query_audit` is kept
as is and still written on every request, so admin audit behaviour does not change.

## API

All routes require `current_user` and only ever see rows where `chats.user_id == user.id`. A chat that
does not exist **or belongs to someone else** returns 404 `not_found` (no existence leak).

- `GET /api/chats?limit=50&offset=0` → `{items: [{id, title, updated_at, turn_count}], total}` newest first.
- `GET /api/chats/{id}` → `{id, title, created_at, turns: [{id, turn_index, question, response, status, error, created_at}]}` ordered by `turn_index`.
- `DELETE /api/chats/{id}` → 204; turns cascade.
- `POST /api/query` (changed): body gains optional `chat_id`. Response gains `chat_id` and `turn_id`.
  - No `chat_id` → create a chat (title from the question), owned by the caller.
  - `chat_id` given → must belong to the caller, else 404.
  - The request body's `history` field is **kept for compatibility but ignored when `chat_id` is set**.
    Conversation context is built server-side from the chat's last 3 turns that have `response.answerable` true
    (question + `response.answer[:4000]`), replacing client-supplied history.
  - The answer cache key stays `None` whenever there is history, exactly as today (first turn of a chat can be cached).
  - Error responses from POST /api/query include "chat_id" inside "error" when a chat exists, so a conversation whose first turn failed can continue.
  - GET /api/chats/{id} merges each turn's stored `cache_hit` into its returned `response` (so a reopened cached answer keeps its badge); chat ids are validated 1..2147483647 (422 otherwise).

## Write path and failure behaviour

The turn is saved in the existing `finally` block of `query`, next to `_audit`, for **every** outcome including
errors (so failed questions show in the chat with a friendly message chosen from the saved status; the raw internal error is stored but not displayed). The chat row is created *before* answering so
`chat_id` is known; if saving the turn fails, it is logged and swallowed — persistence must never break a query,
same rule as `_audit`. `turn_index` is `max(turn_index)+1` computed in the same transaction as the insert; the
unique constraint makes a concurrent double-submit fail one insert (logged, not surfaced).

For raised API errors (422/502/503/504) the client still gets the error as today; the saved turn has
`response = null`, `status`, `error`.

## New code layout

- `backend/app/db/models.py`: add `Chat`, `ChatTurn`.
- `backend/app/api/chats.py` (new): the three routes above, registered in `app/main.py`.
- `backend/app/chats.py` (new, small): `create_chat`, `load_context(chat_id, user_id)`, `save_turn(...)`
  used by `api/query.py` so route handlers stay thin.
- `backend/app/api/query.py`: wire `chat_id`, server-side history, `save_turn` in `finally`.
- Frontend: `api/types.ts` (chat types), `pages/Ask.tsx` (use `chat_id`, stop sending history when a chat exists,
  load a chat's turns), new `components/ChatSidebar.tsx` (list, select, delete, "New conversation").

## Frontend behaviour

- Ask page shows a sidebar of the user's chats (title, relative date). Selecting one fetches
  `GET /api/chats/{id}` and renders its turns; failed turns show a friendly message chosen from the saved status (the raw internal error is stored but not displayed).
- "New conversation" clears `chat_id` and the turn list (no network call; the chat is created on first question).
- After the first answer, the returned `chat_id` is kept so follow-ups append to the same chat, and the sidebar
  list refreshes (react-query invalidation).
- Delete asks for confirmation, then removes the chat; if it was open, the page resets.

## Security and privacy

- Ownership enforced in every query via `user_id` filter (not only in the handler guard).
- Response JSON is data the user already received; no new data is exposed.
- Chats contain business data answers, so they are never returned by admin endpoints.
- Deleting a user cascades to their chats and turns.

## Testing

Backend integration (`backend/tests/integration/test_chats_api.py`, fake LLM as in existing query tests):
1. First question creates a chat with the right owner/title and a turn; response carries `chat_id`, `turn_id`.
2. Follow-up with `chat_id` appends turn_index 1 and the planner receives server-built history.
3. List and get return only the caller's chats; another user's id → 404 for get, delete and `POST /api/query`.
4. Failed query (forced `LLMError`) still saves a turn with `status`, `error`, null response.
5. Delete cascades turns.
6. A save failure (monkeypatched) does not change the query response.
Existing `test_query_api.py` must still pass unchanged.
Frontend: component test for the sidebar (list, select, delete confirm) and `Ask` sending `chat_id` after the first answer.

## Risks / open choices

- `response` JSONB can grow table size; revisit with retention (e.g. delete chats older than N days) if needed.
- No migration tool: `create_all` creates the new tables on startup but would not alter them later; future column
  changes will need Alembic or manual SQL.
- Ignoring client `history` when `chat_id` is set is a behaviour change for any other API client; the bundled
  frontend is the only known consumer.
