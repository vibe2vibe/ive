# IVE End-to-End Flow Testing — Findings

Backend tested at http://localhost:5111. Workspace `b8ae0959-...` (`/Users/michaelramich/Downloads/test`).

## Flows that work

- **Pipeline CRUD + run lifecycle** — `POST /api/pipelines` (custom 2-stage manual pipeline) returned 200 with full definition. `POST /api/pipeline-runs` 200 → `running` with current_stages `["s1"]` and stage_history seeded. `PUT /api/pipeline-runs/:id {action:"cancel"}` 200, status flipped to `cancelled` with `completed_at` populated. `_active_runs` and `_pipeline_task_ids` cleanup paths in `pipeline_engine.py:359-389` exercised. `DELETE /api/pipelines/:id` returned `{ok:true}`.
- **WebSocket `pipeline_run_update` events** — Connected to `/ws`, observed two updates for run `ad168a51`: `status=failed` then `status=cancelled` (both with `current_stages=["s1"]`). Broadcast wiring at `pipeline_engine.py:1285-1291` works.
- **Memory hub-and-spoke** — `GET /api/workspaces/:id/memory` returns hub state with `providers.{claude,gemini}` blocks. `GET /memory/diff` returned `{}` (no divergence). `POST /memory/sync` returned `{status:"up_to_date"}`. `POST /memory/resolve` with `{resolved_content,providers:["claude"]}` returned `status:"synced"` and updated both claude and gemini providers despite only `claude` being requested (see surprises). `PUT /memory/settings` round-tripped correctly.
- **Memory entry CRUD** — `POST /api/memory` with `{name,type,content,workspace_id}` 200 with new id; `DELETE` 200. Required-field error message at `server.py` is helpful.
- **Hook event endpoint** — `POST /api/hooks/event` with `X-Commander-Session-Id` header returned 200 for `SessionStart`, `UserPromptSubmit`, `Stop`, `PreToolUse`, `Notification`, `PreCompact`, and unknown events (silent log). Missing header silently ignored as documented at `hooks.py:2048-2050`. Native event names mapped via `_EVENT_HANDLERS` table at `hooks.py:2032-2037`.
- **Skill install + uninstall** — `POST /api/skills/install` with `{name,content,scope:"user"}` returned 201 and wrote `~/.claude/skills/e2e-test-skill` and `~/.gemini/skills/e2e-test-skill`. `POST /api/skills/uninstall` returned 200 with `removed:true` for both.
- **Plugin catalog + idempotent install** — `GET /api/plugins` returns `builtin-deep-research` with `installed:1`. Re-install correctly errors `400 already installed`.
- **Distill job** — `POST /api/sessions/:id/distill {type:"guideline"}` returned 200 with `{job_id, status:"started", artifact_type:"guideline"}` (route at `server.py:4231`).
- **Commander session retrieval** — `GET /api/workspaces/:id/commander` returned the existing `Commander (Claude Code) — test` session (id `fa10d729...`).
- **Cascade run lifecycle** — `POST /api/cascade-runs` started a run; `PUT {action:"stop"}` flipped to `stopped`; `DELETE /api/cascade-runs/:id` 200.

## Bugs found

- **Board-column trigger never fires** (`pipeline_engine.py:1240,1263`). Code does `event_name != str(CommanderEvent.TASK_STATUS_CHANGED)` — `str(enum_member)` returns `"CommanderEvent.TASK_STATUS_CHANGED"`, but `event_name` arrives as the `.value` string `"task_status_changed"` (set at `event_bus.py:118`). Comparison is always unequal so `_trigger_matches` returns False for every `board_column` and `pipeline_complete` trigger. Reproduced by creating an `active` pipeline with `triggers:[{type:"board_column",config:{column:"in_progress"}}]`, moving a task into `in_progress` (event id 3996 was emitted), and finding zero triggered runs. Fix: use `event.value` (Enum is `str` subclass so `==` to a string would also work — `class CommanderEvent(str, Enum)` at `commander_events.py`).
- **Variable injection has no validation** — `POST /api/pipeline-runs` for a pipeline whose stage prompt contains `{needsvar}` and supplying no variables succeeds with 200 and the run starts. The literal `{needsvar}` would be sent to the agent (`pipeline_engine.py:1112-1128` — `replacer` returns `m.group(0)` for unknown keys). The CLAUDE.md docs claim the engine triggers a "dialog or error" for unknown vars, but that's a frontend-only concern; the API does not enforce. Programs hitting the REST API directly will silently send broken prompts.
- **Skill installed to disk but `/api/skills/installed` returns 0** — After `install` wrote both `~/.claude/skills/e2e-test-skill/SKILL.md` and the gemini path (server confirmed via 201 response), `GET /api/skills/installed` returned an empty list. Either listing only reads a subset of dirs or filters by metadata that the disk-only install didn't write. Worth tracing `skills_client.list_installed()` or whichever handler backs `/api/skills/installed`.
- **Auto-created pipeline session has no PTY → run "fails" before pause is observable** — When a stage references `session_type:"worker"`, `_resolve_session` falls through to `_auto_create_session` (`pipeline_engine.py:1041-1097`), which inserts a session row but never starts a PTY. `_execute_agent_stage` then hits the `is_alive` check at `pipeline_engine.py:549-552` and immediately fails the run. Net effect: every manually-started pipeline using auto-resolved sessions fails on stage 1 unless the caller pre-starts a PTY for that session. The cascade runner has the identical failure mode (`PTY not alive`).
- **Pause/resume return 200 with stale "failed" payload** — `pause_run` at `pipeline_engine.py:311-327` only updates rows where `status='running'`. When the run already crashed to `failed`, `pause` is a silent no-op but still returns the row with `status:"failed"` (and HTTP 200). Callers cannot distinguish "paused successfully" from "ignored because already terminal". Same for `resume_run` (`pipeline_engine.py:330-356`).

## Behavior surprises

- **Memory resolve ignores the `providers` filter argument**. Sending `{resolved_content,providers:["claude"]}` updated both `claude` and `gemini`. Either the API ignores the filter or the underlying `memory_sync` writer always writes all providers.
- **Distill route requires `type` body** even though the CLAUDE.md docs imply "summarize the session" is a single-shot call. Valid types are `guideline|prompt|cascade` per `_DISTILL_PROMPTS`.
- **31 pipeline definitions exist** (mostly preset duplicates and earlier test fixtures). `ensure_presets()` creates new uuids on each run if `preset_key` lookup misses; many copies of `RALPH Pipeline`, `TestSuite-Pipeline`, etc. in the DB suggest historical re-runs left orphans.
- **All branch-named sessions have `worktree=0` and `worktree_path=None`** — they share `branch_group` UUIDs but no actual git worktree was provisioned. The `(branch)` suffix appears decorative for these test fixtures. Real worktrees would need test setup outside REST.
- **Hook handler always returns `{}` HTTP 200** — even for unknown event names, malformed payloads are the only way to get a non-200 response. There's no echo of "did this dispatch?" — observability requires watching logs.

## Couldn't test

- **End-to-end pipeline stage execution** — Every auto-created session was DOA (no PTY). To meaningfully exercise `_execute_agent_stage` would require pre-starting the PTY through `WS start_pty`, which spins up a real `claude`/`gemini` subprocess (out of scope per the warning).
- **Cascade variable substitution + auto-approval** — Same root cause: cascade runner needs a live PTY; the test session's PTY had exited.
- **Worktree paths differ from workspace path** — No real worktree sessions exist in the DB to compare against.
- **`pipeline_complete` cross-pipeline triggers** — Same enum-stringification bug as `board_column`; no point chaining a real run when the listener never matches.
- **Distill result delivery** — Job started (job_id returned) but no `distill_done` event observed within the test window; the LLM router needs a working CLI to complete.
- **Plugin install with multiple plugins** — Catalog only contains `builtin-deep-research`, already installed; no other registered plugin to exercise the fresh install + attach + uninstall happy path. `POST /api/plugins/registries/sync` would be needed first to populate the catalog from the registry URL `https://registry.commander.dev/v1/index.json`.
- **MCP tool `report_pipeline_result`** — Lives in `worker_mcp_server.py:268`; cannot be invoked without a worker session running the stdio MCP.
