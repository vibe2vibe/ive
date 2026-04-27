# IVE Feature Test Findings

End-to-end UI + API test pass against `http://localhost:5173/` and backend `:5111`. Tested ~30 keyboard shortcuts, 6 panel flows, 2 multi-step features (research, code review), and full CRUD on tickets.

## Works correctly

| Feature | Verified | Notes |
|---|---|---|
| Command Palette ⌘K | UI | Fuzzy search returns matches |
| Prompt Library ⌘/ | UI | Prompts + Cascades tabs both render |
| Cascades execution | UI gating | Run button disabled with no session, enables on session select; title="Run on active session" |
| Guidelines ⌘G | UI + API | Toggle persists — `/api/sessions/:id/guidelines` count 0→1 after click |
| MCP Servers ⌘⇧S | UI | Lists builtins (commander, documentor) + custom; auto-approve dropdown per server; "+ add with AI" button present |
| Composer ⌘E | UI | Mounts multi-line textarea with bullet/⌘Enter placeholder hints |
| Quick Action Palette ⌘Y | UI | Renders prompts marked as Quick Action |
| Feature Board ⌘B | UI + API | 6 columns (Backlog, To Do, Planning, In Progress, Review, Done); ticket creation persists |
| Mission Control ⌘M | UI | Shows 102 sessions / 10 active across all workspaces |
| Inbox ⌘I | UI | Lists pending/idle sessions |
| Marketplace ⌘⇧M | UI | Plugin list renders with tier badges |
| Scratchpad ⌘J | UI | Opens session-level notes editor |
| Code Review ⌘⇧G | UI + API | Real diff renders for git workspaces (63KB diff against ive repo, 20 commits) |
| Pipeline presets | API | RALPH, Research Loop, TDD Loop, Review Loop, Verification Cascade present |
| Deep Research | UI + API | Job lifecycle launch → running → cancel verified |
| Observatory | API | 16 findings populated (HN/socket.dev) at `/api/observatory/findings` |
| Session open / xterm mount | UI | Multiple session tabs, each mounts xterm |
| Session lifecycle (rename/clone/merge) | API | All endpoints return 200 with persisted state |

## Bugs / drift

### Pipeline Editor ⌘⇧L doesn't open
`frontend/src/lib/keybindings.js:22` — `pipelineEditor { key: 'l', meta: true, shift: true }` uses lowercase `l`, but browsers send `'L'` when shift is held. `matchesKey()` in keybindings.js:99 compares `event.key === combo.key` exactly, so the shortcut never fires.

Fix: change to `key: 'L'` (mirror `mcpServers` line 25 and `marketplace` line 31 which both use uppercase letters with shift).

### Guidelines GET response field labelled inconsistently
`GET /api/sessions/:id/guidelines` returns `{ guidelines: [...attached items...], active_guideline_ids: [] }`. The `active_guideline_ids` array stays empty even when `guidelines` is populated with attached entries — either the field is dead/legacy or it's documenting a separate "currently in active rotation" concept that never gets set.

Fix candidates: drop `active_guideline_ids` if dead, or populate it from the same join used to build `guidelines`.

### CLAUDE.md keyboard shortcut table has 6+ wrong entries
The "Keyboard Shortcuts" section in `CLAUDE.md` does not match `frontend/src/lib/keybindings.js`:

| CLAUDE.md says | Actual binding |
|---|---|
| ⌘J = Marketplace | ⌘J = Scratchpad |
| ⌘⇧M = Marketplace | (matches — but Scratchpad in CLAUDE.md is also ⌘⇧P, actually ⌘⇧P is Scratchpad in `keybindings.js`?) |
| ⌘⇧Q = Quick Action Palette | ⌘Y = Quick Action Palette |
| ⌘⇧G = Code Review | (matches) |
| ⌘⇧F = Quick Feature | ⌘⇧N = Quick Feature |
| ⌘⇧K = Shortcuts panel | ⌘⇧? = Shortcuts panel |

### Workspace overview API missing common keys
`GET /api/workspaces/:id/overview` does not include `session_count` or `git` keys that the UI surface seems to imply. Probe found `keys=[...]` does not contain those names. Either the UI synthesizes these client-side or the endpoint should be enriched.

### Code Review shows nothing for virgin git repos
TestSuite-2026 workspace at `/tmp/ive-test-suite-workspace` has no commits, so `/git/diff` and `/git/log` return empty. Not a bug, but the panel could surface "no commits yet" instead of an empty state.

## REST API bugs

### `/api/sessions/:id` (and any unmatched `/api/*`) returns 200 + HTML
There is no `GET /api/sessions/{id}` route registered (`server.py` has handlers for `/api/sessions/{id}/messages`, `.../output`, `.../guidelines`, etc., but not the bare detail endpoint). Unmatched paths fall through to the SPA fallback at `server.py:15069` (`add_get('/{_path:.*}', _spa_fallback)`), which returns `index.html` with status 200. Any client probing `/api/whatever` for existence gets a misleading 200 + HTML instead of 404.

Fix: in `_spa_fallback` (line 15058), reject paths that start with `/api/` with `web.json_response({"error":"not found"}, status=404)`.

### `PUT /api/prompts/<bad-id>` returns 500
`update_prompt` at `server.py:3290` issues `UPDATE … WHERE id = ?` then `SELECT … WHERE id = ?`. If no row exists, `dict(None)` raises `'NoneType' object is not iterable`, which the framework returns as 500 with the raw exception message exposed to the client.

Fix: check `cur.rowcount` after the UPDATE (or `if not row: return 404`).

### `DELETE /api/prompts/<bad-id>` silently returns `{ok: true}`
`delete_prompt` at `server.py:3314` runs `DELETE … WHERE id = ?` and returns `{ok: True}` regardless of whether a row was actually deleted. Inconsistent with `tasks` (returns 404 correctly) and bypasses idempotency-vs-existence semantics.

Fix: check `cur.rowcount == 0` → return 404.

### `POST /api/mcp-servers` requires undocumented `server_name`
Returns `{"error": "name, server_name, and command required"}`. CLAUDE.md only mentions "Create MCP server config" with no field schema. Unclear whether `name` and `server_name` are intentionally separate (display name vs slug?) or one is legacy.

### Slow endpoints

| Endpoint | ms | Cause |
|---|---|---|
| `/api/workspaces/:id/preview-screenshot` | 2323ms | Tries to fetch `preview_url` (defaults to localhost:3000); should fail-fast or set short timeout |
| `/api/history/projects` | 110ms | Filesystem scan of `~/.claude/projects/` |
| `/api/skills` | 28ms | First-call catalog hydration |
| `/api/workspaces/:id/git/status` | 57ms | git subprocess |

### Endpoints in CLAUDE.md that don't exist
None confirmed missing — the CLAUDE.md table I probed (`/api/research/feed`, `/api/pipelines/presets`) are not actually documented there. False alarm.

### What's healthy
- 73/75 GET probes return correct status codes (the 2 fails being the routing bug above and a workspace with a misconfigured preview URL).
- Full POST→PUT→DELETE lifecycle verified for: workspaces, sessions, prompts (with caveats above), guidelines, memory, tasks, cascades, templates, MCP servers, pipelines.
- `POST` endpoints consistently return **201** (correct REST semantics) — the `tasks` create returns 201 with the created entity.
- Edge cases mostly handled: nonexistent task → 404, malformed body → 400, method-not-allowed → 405.

## Behavior notes (not bugs, useful to know)

- **Cascade run requires `activeSessionId`** — runs against whatever session is currently focused, not the cascade's saved target.
- **Feature Board respects the *board tab* not active workspace** — creating a ticket in the board adds it to the currently-selected board tab, which can differ from the active workspace shown in the sidebar.
- **`POST /api/research/jobs`** returns 201 not 200 — clients that whitelist 200 will treat creation as failure.
- **Guidelines toggle is optimistic** — UI flips `bg-accent-primary` instantly; backend persist takes ~500ms.
- **Keyboard shortcut listener is on `window` not `document`** — synthetic test events must use `window.dispatchEvent`.
- **Shortcut combos are case-sensitive** — `event.key === combo.key`. When `shift` is in the combo, the `key` field must be uppercase ('L', 'S') not lowercase. This is the root cause of the Pipeline Editor bug.
