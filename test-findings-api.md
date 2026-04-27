# IVE Backend REST API — Audit Findings

Audit performed against `http://localhost:5111`. Backend handlers in `/Users/michaelramich/Documents/ive/backend/server.py`.

---

## Bugs Found

### B1. CRITICAL — Account snapshot creates 2 GB of files for non-existent account ID
- **Endpoint**: `POST /api/accounts/{id}/snapshot`
- **Repro**: `curl -s -X POST http://localhost:5111/api/accounts/00000000-0000-0000-0000-000000000000/snapshot`
- **Expected**: `404 account not found`
- **Actual**: `200 OK` after **7.15 s**, body `{"ok":true,"files":11060,"size_bytes":1996076933,"path":"/Users/michaelramich/.ive/account_homes/<bogus-id>/.claude"}`. Server copies the entire `~/.claude/` directory (1.99 GB on this machine) for any UUID an unauthenticated client sends.
- **Root cause**: `server.py:8704` `snapshot_account` calls `snapshot_current_auth(acc_id)` without first checking that the row exists. `account_sandbox.snapshot_current_auth` happily creates a new directory.
- **Impact**: Trivial disk-fill DoS; unbounded resources consumed per request.

### B2. HIGH — Several PUT handlers crash with 500 on non-existent IDs
Same root cause as the documented `update_prompt` bug — handler runs `UPDATE ... WHERE id=?`, then re-fetches the row and calls `dict(row)` without a `None` check.
- `PUT /api/research/{bad}` → 500 `{"error":"'NoneType' object is not iterable"}` — `server.py:6500-6519` (`update_research`)
- `PUT /api/guidelines/{bad}` → 500 same error — `server.py:3405-3430` (`update_guideline`)
- `PUT /api/sessions/{bad}` → 500 same error — `server.py:4715-4758` (`update_session`)
- `PUT /api/sessions/{bad}/rename` → 500 same error — `server.py:4697-4712` (`rename_session`)
- `PUT /api/workspaces/{bad}` → 500 same error — `server.py:2878-2912` (`update_workspace`)
- `PUT /api/workspaces/{bad}` with no allowed fields actually does return 400 ("no fields"); the 500 path triggers when allowed fields are present.
- **Fix**: After `row = await cur.fetchone()`, check `if not row: return web.json_response({"error":"not found"}, status=404)`.

### B3. HIGH — `POST /api/sessions` returns 500 on bogus workspace_id
- **Endpoint**: `POST /api/sessions`
- **Repro**: `curl -s -X POST http://localhost:5111/api/sessions -H 'Content-Type: application/json' -d '{"workspace_id":"00000000-0000-0000-0000-000000000000","name":"x"}'`
- **Expected**: `400 invalid workspace_id` or `404 workspace not found`
- **Actual**: `500 {"error":"FOREIGN KEY constraint failed"}` — leaks DB schema.
- **Root cause**: `server.py:2983-3022` (`create_session`) never SELECTs the workspace; relies on FK to fail at INSERT.

### B4. HIGH — `POST /api/tasks` with bogus workspace_id → 500
- Same FK-leak pattern as B3 for tasks. `create_task` at `server.py:5293`.
- Repro: `POST /api/tasks {"workspace_id":"00000000-...","title":"x"}` → `500 FOREIGN KEY constraint failed`.

### B5. HIGH — `POST /api/tasks/{bogus}/attachments` accepts uploads to non-existent tasks
- **Repro**: multipart upload to `/api/tasks/00000000-0000-0000-0000-FFFFFFFFFFFF/attachments` with any file.
- **Actual**: `201 OK`. Creates `~/.ive/attachments/00000000-0000-0000-0000-FFFFFFFFFFFF/<file>`. Orphan filesystem pollution; another disk-fill vector.
- **Root cause**: `server.py:6374-6409` (`upload_attachment`) — never queries the tasks table.
- **Note**: Filename sanitization at L6394 strips `/`, `\`, `..` correctly (`../../../etc/passwd` → `......etcpasswd`), so traversal itself is blocked, but task existence is not validated.

### B6. HIGH — Filter param mismatch: `?workspace_id=` is silently ignored on `/api/sessions`, `/api/tasks`, `/api/research`
- Handlers read `?workspace=` (no `_id`):
  - `list_sessions` — `server.py:2929` `request.query.get("workspace")`
  - `list_tasks` — `server.py:5268` same
  - `list_research` — `server.py:6453` same
  - `search_research` — `server.py:6576` same
- CLAUDE.md documents `?workspace_id=` for these endpoints. Concrete proof:
  ```
  GET /api/sessions          → 104 results
  GET /api/sessions?workspace=<id>     → 69 (filter applied)
  GET /api/sessions?workspace_id=<id>  → 104 (param ignored, no filter)
  ```
- **Impact**: Frontends sending `workspace_id` (the documented name) get unfiltered results — cross-workspace data leakage in UI, violates the docs.

### B7. MEDIUM — `?active` filter on `/api/pipeline-runs` only matches literal `"1"`
- **Root cause**: `server.py:10300` `active_only = request.query.get("active") == "1"`
- Probe: `?active=true` returns same 5 rows as no filter; `?active=1` returns 0; `?active=false` returns same 5. Effectively the filter is broken for any value other than the literal `"1"`.

### B8. MEDIUM — `DELETE /api/research/{bad}` and `DELETE /api/cascades/{bad}` return 200 silently
- **Repro**: `curl -X DELETE http://localhost:5111/api/research/00000000-0000-0000-0000-000000000000` → `200 {"ok":true}`
- Same for `/api/cascades/{bad}` and `/api/workspaces/{bad}` and `/api/sessions/{bad}`.
- **Root cause**: e.g. `server.py:6562-6570` `delete_research` runs `DELETE WHERE id=?` and returns `{"ok":true}` regardless of `cur.rowcount`. Same as known `delete_prompt` bug, but undocumented.

### B9. MEDIUM — `DELETE /api/pipeline-runs/{id}` returns 405 (route not registered)
- Documented routing for `pipeline-runs` registers GET, POST, PUT only. No `add_delete` at `server.py:14931-14935`.
- CLAUDE.md doesn't claim DELETE — but `/api/cascade-runs/{id}` does have DELETE while `/api/pipeline-runs/{id}` doesn't, an inconsistency.

### B10. MEDIUM — `POST /api/workspaces` allows duplicate-path "creations" that silently dedupe
- **Repro**:
  ```
  POST /api/workspaces {"path":"/tmp/foo","name":"first"}   → 201, id=A, name="first"
  POST /api/workspaces {"path":"/tmp/foo","name":"DIFFERENT"} → 201, id=A, name="first"  (unchanged)
  ```
- **Root cause**: `server.py:2866-2873` uses `INSERT OR IGNORE` then SELECTs by `path`. Caller has no idea the request was a no-op; gets the original row with original name.
- Same behavior produces a misleading `201` for clearly invalid payloads — `name=12345` returns 500, but `name="x"*100000` returns 201 (silently ignored — dedupes existing `tmp` workspace).

### B11. MEDIUM — `POST /api/workspaces` with `name=12345` (number) → 500
- **Repro**: `{"name": 12345, "path":"/tmp"}` → `500 {"error":"'int' object has no attribute 'strip'"}`
- **Root cause**: `server.py:2852` `body.get("name", "").strip()` assumes string.

### B12. MEDIUM — Invalid JSON / missing Content-Type → 500 instead of 400
- `POST /api/workspaces` with body `{invalid json` → `500 {"error":"Expecting property name enclosed in double quotes..."}`
- POST with raw bytes and no Content-Type → `500 {"error":"Expecting value: line 1 column 1 (char 0)"}`
- **Root cause**: handlers call `await request.json()` which raises and gets caught by the generic `cors_middleware` exception handler at `server.py:12273-12280` which always returns 500.

### B13. MEDIUM — `GET /api/sessions/{bad}/output` returns 200 with empty payload (should be 404)
- **Repro**: `GET /api/sessions/00000000-0000-0000-0000-000000000000/output` → `200 {"session_id":"00000000-...","lines":100,"text":""}`
- Confirms session existence via empty text instead of returning a 404. Same pattern: `/captures`, `/subagents`, `/turns`, `/messages`, `/queue`, `/plugin-components`. Several silently return `[]`.

### B14. MEDIUM — `POST /api/sessions/{bad}/distill` returns wrong-shaped 400
- **Repro**: `POST /api/sessions/{any-id}/distill` with empty body → `400 {"error":"type must be one of: guideline, prompt, cascade"}`. CLAUDE.md (line ~ "POST /api/sessions/:id/distill") doesn't mention `type` field is required.
- With `type=prompt` against bogus id → `404 {"error":"No conversation content found for this session."}` — not a real "session not found" check, just a side-effect of empty conversation.

### B15. MEDIUM — `POST /api/pipeline-runs` returns generic 400 on invalid pipeline_id
- **Repro**: `{"pipeline_id":"00000000-0000-0000-0000-000000000000"}` → `400 {"error":"failed to start"}`. Caller has no way to know whether pipeline doesn't exist or another reason. `server.py:10318-10319`.

### B16. LOW — `PUT /api/settings/{key}` accepts `null` value but rejects non-string types inconsistently
- `{"value":"hi"}` → 200 stored
- `{"value":null}` → 200 stored as null
- `{"value":42}` → 400 `value must be a string`
- `{"value":true}` → 400 same
- Missing `value` field → 200 stored as null (no validation that the field was provided).
- Inconsistent: null is accepted, but other non-string scalars are rejected with a misleading message ("JSON-encode complex values").

### B17. LOW — Bogus settings keys are accepted with no allow-list check
- `PUT /api/settings/anything_random_xyz` returns 200 and persists. CLAUDE.md implies a known-keys catalog (`/api/settings/experimental` lists known ones). No validation that the key is known. Garbage keys accumulate in DB indefinitely.

### B18. LOW — `POST /api/hooks/event` swallows arbitrary payloads silently
- Empty body, garbage body, non-existent session_id all return `200 {}`. Useful intentionally for permissive hook delivery, but surprising — there is no signal that the event was malformed/dropped. `server.py:15016`.

### B19. LOW — `POST /api/hooks/pipeline-result` requires `session_id` but reports it last
- Empty body → `400 session_id required` even when `result` field is missing. Order of validation is OK; field name is undocumented in CLAUDE.md. The MCP `report_pipeline_result` tool uses these.

### B20. LOW — `GET /api/plan-file` requires `?path=`, not `?workspace_id=` as implied in user-facing docs
- `GET /api/plan-file` (no qs) → `400 missing path`
- The audit prompt suggested it needs `workspace_id` — actually it needs `path` (`server.py:12235-12245`). No way to look up a workspace's plan.md without already knowing the path.

---

## Validation Gaps

| Field | Endpoint | Issue | server.py |
|---|---|---|---|
| `cli_type` | `POST /api/sessions` | Accepts any string (e.g. `"vim"`) → 201 | 2990 |
| `model` | `POST /api/sessions` | Accepts any string (e.g. `"gpt-4"`) → 201 | 2992 |
| `permission_mode` / `effort` | `POST /api/sessions` | No validation of allowed values | 2993-2994 |
| `name` | `POST /api/sessions` | No length limit (1 MB names accepted) | 2991 |
| `name` | `POST /api/workspaces` | Type-checked at `.strip()` only — number → 500 | 2852 |
| `workspace_id` FK | `POST /api/sessions`, `/tasks` | No pre-check, FK leak as 500 | 3018, 5293 |
| `task_id` | `POST /api/tasks/{id}/attachments` | No existence check, orphan dirs created | 6374 |
| `account_id` | `POST /api/accounts/{id}/snapshot` | No existence check, copies 2 GB regardless | 8704 |
| Settings `value` type | `PUT /api/settings/{key}` | Inconsistent: null OK, int/bool rejected | (around general settings handler) |

---

## Documentation Drift (CLAUDE.md vs reality)

1. **Filter param naming** (B6): CLAUDE.md and the prompt list `?workspace_id=` for sessions/tasks/research; backend uses `?workspace=`. This is the single most consequential drift — UI clients silently get unfiltered data.
2. **POST /api/sessions/{id}/input field** is `message` not `data`. CLAUDE.md WS protocol uses `data`; REST uses `message`. (`server.py:5959`).
3. **POST /api/research/jobs** requires `query` (not `topic`). Empty body → `400 query required` (`server.py:11890` area).
4. **POST /api/mcp-servers** requires undocumented `server_name` AND `command` fields (already noted in known bugs).
5. **POST /api/cascades** requires `steps: non-empty array`. CLAUDE.md says just `steps` is acceptable.
6. **GET /api/plan-file** uses `?path=`, not `?workspace_id=` (B20).
7. **`/api/pipeline-runs?active=`** documented as boolean; backend only matches literal `"1"` (B7).
8. **DELETE /api/pipeline-runs/{id}** is documented as a state action via `PUT` only — no DELETE route exists (returns 405).
9. **Pipeline run `update` action** accepts `cancel`/`pause`/`resume` only; `{}`/garbage → 400 `unknown action: None` (good behavior, but undocumented enum).
10. **Session lifecycle endpoints** (`/output`, `/captures`, `/subagents`, `/turns`, `/messages`, etc.) return 200 with empty array/object for non-existent sessions; CLAUDE.md does not specify 404 contract.
11. **`/api/sessions/{id}/messages`** silently returns `[]` for non-existent sessions — documented as "get conversation history" with no mention.
12. **`POST /api/hooks/event`** payload shape is not documented in CLAUDE.md.

---

## Performance Issues

- **B1 (snapshot_account)**: 7.15 s blocking copy of 1.99 GB triggered by single unauthenticated POST. No streaming, no cancellation, no existence check.
- **`GET /api/search?q=x` + 4 search endpoints**: Each search builds `LIKE '%q%'` query against multiple TEXT columns with no FTS index. With 100+ sessions and many messages, this scales linearly and gets slower over time. ~35 ms today; fine but unbounded.
- **Generic exception handler** at `server.py:12273-12280` (`cors_middleware`) catches all uncaught exceptions and turns them into 500. This swallows the wrong-shape feedback loop — every `'NoneType'` 500 should be a 404.
- **Distill / research-jobs / docs-build** endpoints fire-and-forget background subprocesses, returning quickly. No status-poll endpoint surfaced for short-job tracking; only WS progress events.

---

## Security Notes

- **CORS**: `Access-Control-Allow-Origin: *` set unconditionally when `AUTH_TOKEN` is unset (`server.py:12290`). Acceptable for localhost-only mode, but the wildcard plus `Allow-Methods: ..., DELETE` means any browser-loaded page can hit destructive endpoints if reachable. The multiplayer mode (`AUTH_TOKEN` set) correctly restricts to request origin.
- **Path traversal**: properly handled in `serve_attachment` (`server.py:6418-6421`) and `_resolve_plan_path`. `upload_attachment` filename sanitizer at L6394 strips `/` and `..` (good).
- **SQL injection**: All queries audited use parameterized statements; `'; DROP TABLE workspaces;--` as a name was stored as a literal string. No injection found.

---

## What's Healthy

- All probed read endpoints (`GET /api/...`) return JSON with proper Content-Type.
- Most POST handlers correctly return 201 (`/api/prompts`, `/api/guidelines`, `/api/tasks`, `/api/cascades`, `/api/research`, `/api/workspaces`, `/api/sessions`).
- 404 contracts are correct for: `/api/tasks/{bad}`, `/api/research/{bad}` (GET), `/api/pipelines/{bad}`, `/api/pipeline-runs/{bad}`, `/api/cascade-runs/{bad}`, `/api/workspaces/{bad}/overview`, `/api/workspaces/{bad}/agents-md`, `/api/workspaces/{bad}/memory`, `/api/workspaces/{bad}/git/status`, `/api/sessions/{bad}/scratchpad`, `/api/sessions/{bad}/tree`.
- Pipeline run state machine returns clean 400s for unknown actions (`unknown action: obliterate`).
- Concurrent task creation (5 parallel POSTs) all returned 201 with distinct IDs — no race.
- Path traversal in plan-file (`?path=../../../etc/passwd`) and attachment serving correctly rejected with 400.
- Auth/CORS middleware logic is straightforward; multiplayer mode is restrictive.

---

## Test Artifacts

Three throwaway sessions remain in DB (created during validation probing — names `xxxxxxx...`, `x`, `x`, IDs `9618b75e-...`, `bfa4ae0b-...`, `e261b022-...`). They have no PTY and don't consume resources. The bogus `~/.ive/account_homes/00000000-...` snapshot directory was deleted (was 1.99 GB).
