# IVE Master Findings — All Test Tracks Consolidated

Date: 2026-04-28. Backend `:5111`, frontend `:5173`.

Tracks: Frontend UI (in progress) · REST API · WebSocket/PTY · Complex flows · MCP rights/behavior.

---

## CRITICAL severity (fix before any --tunnel/mobile rollout)

### C1. Account snapshot DoS — copies 2 GB for any UUID, unauthenticated
- `POST /api/accounts/{id}/snapshot` → 200 in 7.15 s, body says `files:11060, size_bytes:1996076933`. Anyone reachable can fill the disk with one curl per random UUID.
- Root cause: `server.py:8704` `snapshot_account` calls `snapshot_current_auth(acc_id)` without checking the row exists.
- Fix: SELECT account before snapshot; return 404 if missing.

### C2. WebSocket: no per-WS auth/ownership on `session_id`
- Any WS client may `input`/`stop`/`resize`/`broadcast` to any session_id (`server.py:2485-2554`).
- Currently fine on localhost; **fatal** for the planned `--tunnel`/mobile use case (per `feedback_no_perm_system.md`).
- Same applies to `POST /api/sessions/:id/input`.
- Fix: per-WS session ownership map; reject foreign session_ids with `error` frame.

### C3. WebSocket: bad resize crashes the connection
- `{"action":"resize","cols":99999,"rows":99999}` → server tears down WS with protocol 1002.
- `pty_manager.py:177` calls `struct.pack("HHHH", ...)` which raises `struct.error` for values outside `[0,65535]`. The `except (OSError, ProcessLookupError)` at `:181` doesn't catch it; the WS dispatch loop has no outer try/except.
- PTY survives but is orphaned from the dead WS — silent zombie.
- Fix: clamp cols/rows in `PTYSession.resize`; add `struct.error` to except; wrap dispatch loop in a guard that emits `error` and continues.

### C4. Pipeline triggers `board_column` and `pipeline_complete` never fire
- `pipeline_engine.py:1240,1263` does `event_name != str(CommanderEvent.TASK_STATUS_CHANGED)`. `str(enum_member)` yields `"CommanderEvent.TASK_STATUS_CHANGED"` but `event_name` is the `.value` `"task_status_changed"` (set at `event_bus.py:118`). Comparison always unequal → **the trigger silently never matches**.
- Verified: created pipeline with `triggers:[{type:"board_column",...}]`, moved a task into `in_progress`, event 3996 emitted, zero runs spawned.
- Fix: compare against `CommanderEvent.TASK_STATUS_CHANGED.value` (or use `==` on the Enum since `class CommanderEvent(str, Enum)`).

### C5. `?workspace_id=` filter silently ignored on multiple list endpoints
- Handlers read `?workspace=` while CLAUDE.md and clients send `?workspace_id=`:
  - `list_sessions` `server.py:2929`
  - `list_tasks` `server.py:5268`
  - `list_research` `server.py:6453`
  - `search_research` `server.py:6576`
- Concrete: `/api/sessions?workspace_id=<id>` returns 104 rows (unfiltered); `?workspace=<id>` returns 69 (correct).
- **Frontend silently shows cross-workspace data**.
- Fix: accept both names; or change handlers to read `workspace_id`.

---

## HIGH severity

### H1. Five PUT handlers crash 500 on unknown id
Same root cause as documented `update_prompt` bug. All do UPDATE then `dict(row)` without None check.
- `update_research` `server.py:6500`
- `update_guideline` `server.py:3405`
- `update_session` `server.py:4715`
- `rename_session` `server.py:4697`
- `update_workspace` `server.py:2878`
Plus the original `update_prompt` `server.py:3290`.
Fix: add `if not row: return 404`.

### H2. POST endpoints leak DB schema as 500
- `POST /api/sessions` with bogus `workspace_id` → `500 FOREIGN KEY constraint failed` (`server.py:2983-3022`).
- `POST /api/tasks` same (`server.py:5293`).
- Fix: pre-validate workspace existence; return 400/404.

### H3. Task attachment uploads accept non-existent task IDs
- `POST /api/tasks/<bogus>/attachments` → 201, creates `~/.ive/attachments/<bogus>/<file>`. Disk-fill vector.
- `server.py:6374` `upload_attachment` never queries the tasks table.
- Filename traversal blocked correctly; just no existence check on task_id.

### H4. Connect-time WebSocket fanout (privacy + cost)
- A fresh WS gets ~13 unsolicited `task_update`/`session_*` events from other workspaces immediately after connect.
- `broadcast()` `server.py:350-359` is global fanout to every member of `ws_clients`. No subscription model.
- Multi-client (mobile + desktop) burns bandwidth; multi-user leaks data.

### H5. WebSocket `replay_turns` against bogus session schedules a 90s phantom coroutine
- `{"action":"replay_turns","session_id":"bogus","turns":["hi"]}` emits `status:replaying` for the bogus id, runs up to 90s, emits `status:idle`+`replay_done`, all broadcast to every connected client.
- `server.py:2527` checks `session_id and turns`, never that the session exists.

### H6. Auto-created pipeline/cascade sessions have no PTY → every manual run fails on stage 1
- `_resolve_session` falls to `_auto_create_session` (`pipeline_engine.py:1041-1097`) which inserts a session row but never starts a PTY.
- `_execute_agent_stage` hits `is_alive` check at `pipeline_engine.py:549-552` and fails the run immediately.
- Affects pipelines AND cascades that resolve sessions automatically.

### H7. Skill install writes to disk but `GET /api/skills/installed` returns empty
- After successful install (verified `~/.claude/skills/e2e-test-skill/SKILL.md` and gemini path written), the listing endpoint returns `[]`.
- Either lister reads wrong dirs or filters by metadata the disk-only install didn't write.

---

## MEDIUM severity

### M1. Silent DELETE on unknown id (multiple endpoints)
Returns `200 {"ok":true}` regardless of whether anything was deleted.
- `delete_prompt` `server.py:3314` (known)
- `delete_research` `server.py:6562`
- `delete_cascade`, `delete_workspace`, `delete_session` (same pattern)
Fix: check `cur.rowcount == 0` → 404.

### M2. `?active` filter on `/api/pipeline-runs` only matches literal `"1"`
- `server.py:10300` `active_only = request.query.get("active") == "1"`. `?active=true` returns same as no filter.

### M3. `DELETE /api/pipeline-runs/{id}` not registered → 405
- `add_delete` missing at `server.py:14931-14935`. Asymmetric with `/api/cascade-runs/{id}` which has DELETE.

### M4. `POST /api/workspaces` with duplicate path silently dedupes
- `server.py:2866-2873` uses `INSERT OR IGNORE` then SELECT by path. Returns 201 with the **original** row, ignoring the new payload's `name` etc. Caller can't tell a no-op happened.

### M5. `POST /api/workspaces` with `name=12345` (number) → 500
- `server.py:2852` `body.get("name","").strip()` assumes string.

### M6. Invalid JSON / missing Content-Type on POST → 500 instead of 400
- Caught by generic `cors_middleware` `server.py:12273-12280`.

### M7. Session sub-endpoints return 200 + empty for unknown id (should be 404)
- `/output`, `/captures`, `/subagents`, `/turns`, `/messages`, `/queue`, `/plugin-components`.

### M8. `POST /api/sessions/{id}/distill` requires undocumented `type` body
- Valid types: `guideline|prompt|cascade`. CLAUDE.md doesn't mention.

### M9. `POST /api/pipeline-runs` with bogus pipeline_id → generic 400 "failed to start"
- No way to distinguish "pipeline doesn't exist" from other failures. `server.py:10318-10319`.

### M10. Pipeline variable injection has no backend validation
- `{unknownvar}` in stage prompt is sent as literal text to the agent (`pipeline_engine.py:1112-1128`). CLAUDE.md claims engine triggers a dialog/error — that's frontend-only.

### M11. Pause/resume return 200 with stale state
- `pause_run` (`pipeline_engine.py:311-327`) only updates `WHERE status='running'`. On terminal-state runs returns 200 with `status:"failed"` (or whatever it was). Caller can't distinguish success from ignored.

### M12. Memory `resolve` ignores `providers` filter
- `POST /api/workspaces/:id/memory/resolve {providers:["claude"]}` updates both claude and gemini. Either API ignores the filter or `memory_sync` writer always writes all providers.

### M13. Pipeline Editor ⌘⇧L doesn't open
- `frontend/src/lib/keybindings.js:22` uses lowercase `'l'`. Browsers send `'L'` when shift is held. `matchesKey()` requires exact match.
- Fix: `key: 'L'`.

### M14. `active_guideline_ids` always `[]` in `GET /api/sessions/:id/guidelines`
- `guidelines` array is populated correctly but `active_guideline_ids` field is dead.

### M15. CLAUDE.md keyboard shortcut table has 6+ wrong entries
| CLAUDE.md says | Actual |
|---|---|
| ⌘J = Marketplace | ⌘J = Scratchpad |
| ⌘⇧Q = Quick Action | ⌘Y |
| ⌘⇧F = Quick Feature | ⌘⇧N |
| ⌘⇧K = Shortcuts | ⌘⇧? |
| ⌘⇧P = Scratchpad | (Scratchpad is ⌘J) |

---

## LOW severity

### L1. Settings `value` accepts null but rejects int/bool
- `PUT /api/settings/{key} {"value":null}` 200; `{"value":42}` 400; `{"value":true}` 400. Inconsistent.

### L2. Bogus settings keys persist with no allow-list
- `PUT /api/settings/anything_random_xyz` 200, persists.

### L3. `POST /api/hooks/event` swallows arbitrary payloads with 200
- Empty/garbage/unknown event types all 200. Safe but unobservable.

### L4. `POST /api/hooks/pipeline-result` undocumented field requirements
- Requires `session_id`; CLAUDE.md doesn't say.

### L5. `GET /api/plan-file` requires `?path=` not `?workspace_id=`
- `server.py:12235-12245`. No way to look up a workspace's plan.md without already knowing the path.

### L6. WebSocket protocol section in CLAUDE.md is incomplete
Undocumented WS actions on the same socket:
- `preview_start` / `preview_input` / `preview_navigate` / `preview_resize` / `preview_claim_driver` / `preview_screenshot` / `preview_stop` (`server.py:2560-2683`)
- `hello` / `presence_update` (`server.py:2686-2748`)

Undocumented server-pushed message types: `presence_snapshot`, `presence_join`, `presence_update`, `presence_leave`, `preview_frame`, `preview_navigated`, `preview_driver_changed`, `preview_started`, `preview_error`, `preview_screenshot`, `preview_driver_denied`.

### L7. WebSocket: silent failure modes on unknown ids
`input`/`stop`/`resize`/`broadcast` to dead session_ids silently no-op (`pty_manager.py:244-265`, `server.py:2543`). Hard to debug from clients.

### L8. WebSocket: no message ordering guarantees / sequence numbers
Output is per-session batched at 16 ms (`server.py:362-379`); intra-session order preserved, cross-session order is wall-clock only.

### L9. 31 pipeline definitions in DB (preset duplicates from re-runs)
`ensure_presets()` creates new uuids if `preset_key` lookup misses. Test fixtures and re-runs accumulate.

### L10. All `(branch)` test sessions have `worktree=0`
Decorative naming on test fixtures; no real worktree provisioned.

---

## Frontend UI deep-test (added)

From the deep-test agent against `localhost:5173`:

### F1. `SearchPanel.highlightMatch` is a silent no-op (`SearchPanel.jsx:135-147`)
Function name promises highlighting but only slices and adds ellipses — no `<mark>` or styled-span wrapping. Search results have zero visual emphasis on matched terms.

### F2. Sidebar context menu ignores Escape (`Sidebar.jsx:13-17`)
Registers a `click` outside-listener but not `keydown`. The menu can stack with `⌘M`/other panels because Escape never dismisses it.

### F3. Plan Viewer shows Approve buttons in empty state (`PlanViewer.jsx:641`)
Approve guards on `sessionId` only. Clicking the green button while the empty-state "No plans found" is showing still sends `1`/`2` to the PTY via `sendPlanChoice` — could land in an unrelated menu.

### F4. Sidebar context menu missing "Clone"
CLAUDE.md says context menu offers rename/clone/export/delete; UI only renders rename/export/delete. Either CLAUDE.md is wrong or the Clone item was removed.

(F5 is the same as M15: ⌘⇧L bug, ⌘⇧Q vs ⌘Y, ⌘⇧F vs ⌘⇧N, ⌘⇧K vs ⌘⇧? drift.)

---

## Documentation drift checklist

REST:
- `?workspace_id=` vs actual `?workspace=` (C5)
- `POST /api/sessions/:id/input` field is `message` not `data`
- `POST /api/research/jobs` requires `query` not `topic`
- `POST /api/mcp-servers` requires `server_name` and `command` (not just `name`+`command`)
- `POST /api/cascades` requires `steps: non-empty array`
- `GET /api/plan-file` uses `?path=`
- `?active=` only matches `"1"` (M2)
- No `DELETE /api/pipeline-runs/{id}` (M3)

Frontend:
- 6+ wrong keyboard shortcuts (M15)

WebSocket:
- 9 undocumented actions, 11 undocumented server message types (L6)

---

## Test artifacts left

- 3 throwaway sessions: ids `9618b75e-…`, `bfa4ae0b-…`, `e261b022-…`. No PTY.
- Settings key `audit_test_key_xyz` cannot be deleted (no DELETE route on settings).
- Bogus snapshot dir `~/.ive/account_homes/00000000-...` (1.99 GB) was deleted post-test.

---

## MCP rights & behaviour audit

Tested by enumerating every tool exposed by `mcp_server.py` (commander), `worker_mcp_server.py` (worker/planner/test_worker), and `documentor_mcp_server.py` (documentor); then probing the underlying REST routes that those tools call. The PTY-start wiring at `server.py:2185-2317` resolves attached MCP servers, expands `{session_id}/{workspace_id}/{session_type}` into env vars, and writes the per-session config file. Auto-attach lives at `server.py:3026-3057`.

### Tool surface matrix

| Tool category | Commander | Worker | Planner | Documentor |
|---|---|---|---|---|
| Spawn sessions (`create_session`, `escalate_worker`) | ✅ | — | — | — |
| Stop sessions (`stop_session`) | ✅ | — | — | — |
| Send keystrokes (`send_message`, `broadcast_message`) | ✅ | — | — | — |
| Read any session (`read_session_output`, `get_session_status`, `list_worker_digests`) | ✅ | partial via `list_peers` (W2W only) | partial | — |
| Update ANY task (`update_task`, `create_task`) | ✅ | — | `create_task` only | — |
| Update OWN task (`update_my_task`, `get_my_tasks`) | — | ✅ (scope check `_is_my_task`) | ✅ | — |
| Memory write/search (`save_memory`, `search_memory`) | ✅ | ✅ | ✅ | read-only via `get_knowledge_base` |
| Skills (`search_skills`, `get_skill_content`) | ✅ | ✅ | ✅ | — |
| Pipeline result (`report_pipeline_result`) | — | ✅ | ✅ | — |
| Browser preview/screenshot | ✅ | — | — | ✅ (Playwright direct, not via REST) |
| Docs scaffolding/build/write | — | — | — | ✅ |
| W2W comms (`post_message`, `headsup`, `blocking_bulletin`) | ✅ (commander side) | gated on `workspace.comms_enabled` | gated | — |
| Coord (`coord_check_overlap`/`acquire`/`release`/`peers`) | gated on `experimental_myelin_coordination` | gated × workspace flag | gated | — |
| Experimental (`checkpoint`, `switch_model`) | gated on app_settings | — | — | — |

Worker/planner only differ in one tool (`create_task`) — the planner gate is the env var `WORKER_SESSION_TYPE='planner'` set from `sessions.session_type` column at PTY start.

### MCP scoping is purely advisory — backend enforces nothing (CRITICAL)

The MCP layer's appearance of scoping is misleading. Every probe below was a single `curl` with no auth header, no MCP context, no session token.

#### MCP-S1. Cross-session task hijack (worker → any task) — CONFIRMED
- `worker_mcp_server.py:_is_my_task` filters by `task.assigned_session_id == SESSION_ID` before letting `update_my_task` proceed. But the underlying `PUT /api/tasks/{id}` (`server.py:5382`) has **no ownership check** — any caller can mutate any task.
- Probe: created sessions `sA`, `sB` and tasks `tA`, `tB`; then issued `PUT /api/tasks/{tB} {assigned_session_id: sA, status: "done", result_summary: "hijacked"}`. Returned 200; readback confirmed reassignment + status flip.
- Worker doesn't even need to bypass MCP — it has Bash/Edit tools and can `curl` directly. The MCP tool just makes the legitimate path easy; the bypass is trivial.
- Affects: feature-board integrity, pipeline ownership tracking, planner sub-task ordering.

#### MCP-S2. Planner-gated `create_task` is also bypassable — CONFIRMED
- Worker MCP gates `create_task` on `SESSION_TYPE == "planner"` (`worker_mcp_server.py:1079`). Regular workers don't see the tool in `tools/list`.
- But `POST /api/tasks` (`server.py:5293`) has no role check. Any session can file new tasks regardless of session_type.
- Probe: `POST /api/tasks {workspace_id, title:"rogue-task-from-worker"}` from a non-planner returned 201 with a fresh task ID.
- Same root cause as MCP-S1: the gate is in the MCP definition, not the route.

#### MCP-S3. Worker `tool_list_peers` leaks across all workspaces — CONFIRMED
- `worker_mcp_server.py:126` calls `/sessions?workspace_id={WORKSPACE_ID}`. Combined with C5 (`?workspace_id=` is silently ignored), the call returns every session in every workspace.
- Probe: `GET /api/sessions?workspace_id=<ive>` → 106 sessions across **8 workspaces**.
- Compare: commander's `tool_list_sessions` uses `?workspace=` (singular) which IS honoured — 3 sessions, 1 workspace. So commander's view is correct, but workers see everything.
- Fixing C5 fixes this. Until then, every worker sees every other worker in every workspace.

#### MCP-S4. Worker can stop ANY session — CONFIRMED
- Worker MCP exposes no `stop_session` tool. Commander does (`mcp_server.py:tool_stop_session` → `DELETE /api/sessions/{id}`).
- But `DELETE /api/sessions/{id}` has no auth. Probe killed a sibling session via direct REST. Workers via Bash, or any reachable client, can mass-DELETE sessions.
- Same hole behind C2 (WS no auth) and the disk-fill DoS (C1) — the entire REST surface assumes single-user-localhost.

#### MCP-S5. Worker can switch any session's model — CONFIRMED
- Commander MCP gates `switch_model` behind `experimental_model_switching=on`. Even when the flag is off, `POST /api/sessions/{id}/switch-model` is open.
- Probe: switched a foreign worker session from sonnet to opus by direct REST → 200 ok. Bypasses both the MCP gate and the session ownership.
- Spend implication: an adversary can flip live workers to the most expensive model.

#### MCP-S6. `hooks/event` and `hooks/pipeline-result` accept any forged session_id
- Worker `tool_report_pipeline_result` posts to `POST /api/hooks/pipeline-result` with `session_id: SESSION_ID` (`worker_mcp_server.py:274`). Backend never verifies the caller owns that ID.
- Probe: `POST /api/hooks/pipeline-result {session_id:"forged-12345", status:"pass", ...}` → 200 with `{ok: false, message: "session not in active pipeline"}`. So the forged ID is rejected by lookup, but only because no pipeline run owns it. A forged ID matching a real running pipeline run **would** report a false pass.
- Same for `POST /api/hooks/event` with no `X-Commander-Session-Id` header — accepted, body 200 `{}`. Documented as "silently ignored" in `hooks.py:2048-2050`, but combined with no auth, an attacker can push fake `SessionStart`/`Stop`/`Notification` events that drive UI nudges, plan-ready bells, and oversight prompts.

#### MCP-S7. Memory/save_memory has no per-worker scoping
- Workers and commander both call `POST /api/memory` (`memory_manager`-backed). No `from_session_id` recorded; no workspace gating; the only field is `workspace_id` which the writer chooses.
- A worker can pollute another workspace's memory by passing that workspace_id. Memory then surfaces in other agents' system-prompt injection.
- Recommendation: server should override `workspace_id` from the env var bound to the session, not trust the body.

#### MCP-S8. Documentor `tool_write_doc_page` path-traversal (low-impact, local-only)
- `documentor_mcp_server.py:794` does `os.path.join(DOCS_DIR, args["path"])` after only stripping a leading `/`. `path="../../etc/foo"` writes outside DOCS_DIR.
- Severity: LOW — the documentor process already has Bash/Edit tools to write anywhere; the MCP only documents intent, not enforcement. But: if a future plugin restricts the documentor to MCP-only tools (no shell), this becomes the escape.

#### MCP-S9. Rogue commander session creation does NOT auto-attach commander MCP
- `POST /api/sessions {session_type:"commander"}` returns 201 ok. Inspecting `/api/sessions/{id}/mcp-servers` afterward shows **no** `builtin-commander` attached, even though `server.py:3026-3030` claims to auto-attach via `INSERT OR IGNORE`.
- Probe consistently returned `has builtin-commander attached: False`. Either the path runs only when going through Commander's bootstrap (`/api/workspaces/{id}/commander` at `server.py:7204-7228`), or the INSERT row uses a different join. Worth tracing — the fact that `session_type=commander` doesn't reliably get the commander MCP means the MCP gating story has another loose end.

### MCP wiring observations (informational, not bugs)

- `WORKER_SESSION_ID` is shared by both `worker_mcp_server.py` AND `documentor_mcp_server.py` (`documentor_mcp_server.py:32`). Documentor inherits it because the docs MCP server reuses the worker variable name. Confusing; not exploitable.
- `WORKER_SESSION_TYPE` is set from `sessions.session_type` column. Direct PUT to set `session_type='planner'` is **blocked** by the API (returns 400) — there's a column whitelist for `update_session`. So the planner privilege cannot be self-promoted via REST. Good. (But MCP-S2 makes the gate moot anyway because POST /tasks is open.)
- `mcp_config_template.json` is the legacy single-server template (`commander` only). Real wiring goes through `db.py:SEED_MCP_SERVERS` + per-session join table `session_mcp_servers`. The template file is mostly dead code — only used in extreme fallback paths.
- Commander MCP's `tool_request_docs_update` uses CR (`\r`) when sending the message — required because Claude Code's TUI is in raw mode (LF would not submit). Non-obvious; documented at `mcp_server.py:770`.

### Recommendations

1. **Add an authn boundary at the REST layer.** Per `feedback_no_perm_system.md` the model is "owner + TTL'd device sessions" (not RBAC), so this is a single-secret problem: every REST call must carry the device session token; the WS handshake must bind a token to a connection. Without this, MCP scoping is theatre.
2. **Bind tasks/sessions to an actor on every mutation.** `PUT /tasks/{id}` should reject mutations unless the caller's bound session_id is `assigned_session_id` OR `commander_session_id` OR has the `commander` session_type for this workspace.
3. **Strip body-supplied `workspace_id` from worker-origin calls.** Resolve it from the bound session's row, not the request body.
4. **Fix C5 (`workspace_id` filter)** — also closes MCP-S3 (cross-workspace peer leak) and the wider list-endpoint family.
5. **Sanitize documentor `write_doc_page` path** — `os.path.realpath` + `commonpath` check against DOCS_DIR. Cheap belt-and-suspenders even with shell access.
6. **Investigate MCP-S9** — verify `session_type=commander` reliably attaches `builtin-commander` MCP regardless of creation path. The `auto_start` path may be the only one that wires MCP, leaving a gap when sessions are created lazily.
