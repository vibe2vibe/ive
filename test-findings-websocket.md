# IVE WebSocket Protocol — Test Findings

Probe target: `ws://localhost:5111/ws`. Test sessions: `b633abbe-…` (idle E2E session, started/stopped by tests). All tests run with `websockets==15.0.1`.

## What works (verified protocol behaviors)

- **`start_pty` happy path** emits `status:running` then `output` (server.py:2393-2412 area; cached-output replay at server.py:1665-1675). Verified: 2 messages received, status first, output non-empty.
- **`start_pty` with unknown session_id** returns a typed `{"type":"error","message":"Session not found"}` (server.py:1679-1683). Verified.
- **`stop` happy path** terminates the PTY and emits `{"type":"exit","code":0}` (pty_manager.py:117-128 → `_on_exit` → broadcast). Verified.
- **`resize` with sane values** is silent and idempotent (no ack). Confirmed via `struct.pack("HHHH", rows, cols, 0, 0)` at pty_manager.py:177.
- **`input` with empty string** is silently skipped (server.py:2488 `if session_id and input_data`). Safe.
- **Malformed JSON** is silently dropped (server.py:1644-1646 `json.loads` in try/except → `continue`). No disconnect.
- **Unknown action names** (`"frobnicate"`) are silently dropped (no `else` branch).
- **`input` with 1 MB payload** is accepted on the wire; `pty_manager.write` drains via retry loop (pty_manager.py:154-168).
- **Rapid reconnect (8x sequential)** all succeed cleanly.

## Bugs / protocol issues

### BUG 1 — Resize with cols/rows outside `[0, 65535]` kills the WebSocket
- **Action:** `{"action":"resize","session_id":"<alive>","cols":99999,"rows":99999}` (also reproduces with `-1` or `>65535`).
- **Expected:** clamp / reject / send error frame. PTY behavior should be best-effort.
- **Actual:** Connection is closed by the server with WebSocket protocol error `1002 reserved bits must be 0; no close frame received`. Subsequent sends raise `ConnectionClosedError`. The PTY itself remains alive (zombie from this client's perspective — verified the session was still `running` after the WS died, until cleaned up via fresh WS+`stop`).
- **Root cause:** `pty_manager.py:177` calls `struct.pack("HHHH", rows, cols, 0, 0)`. Format `H` requires `0 <= n <= 65535`, raising `struct.error` for negatives or values above 65535. The surrounding `try/except (OSError, ProcessLookupError)` at `pty_manager.py:181` does NOT catch `struct.error`.
- **Propagation:** The exception escapes `pty_mgr.resize()` (server.py:2535). The WS handler's `async for msg in ws:` loop has NO `try/except` around action dispatch (server.py:1641-2750), so the exception unwinds to the outer `try/finally` (server.py:2752), aiohttp tears down the connection mid-frame, and the peer sees `1002`.
- **Fix:** clamp cols/rows in `PTYSession.resize` (e.g. `max(1, min(cols, 9999))`) and/or wrap action dispatch in try/except that emits `error` and continues.
- **Severity:** any browser tab can crash its own WS by sending one bad resize. Worse: the server is single global `ws_clients`, not per-client state — so the resize crash leaves the PTY orphaned without UI feedback.

### BUG 2 — No auth/ownership on `session_id` (WS-level CSRF)
- **Action:** Any connected WS may send `input`/`stop`/`broadcast`/`resize` for any `session_id` in the DB.
- **Where:** server.py:2485-2554 — every action dispatches purely on `data.get("session_id")` with no check that this WS opened the session, knows a token, or even shares a workspace.
- **Impact:** assumes the server is single-user-localhost. Per CLAUDE.md and `feedback_no_perm_system.md`, the planned mobile/`--tunnel` deployment exposes the server beyond localhost. With no per-WS identity, any reachable client can `stop` or inject keystrokes into any session. Same applies to REST `/api/sessions/:id/input`.
- **Note:** `ws_peers` (server.py:45) tracks presence but is not used as an auth boundary.

### BUG 3 — Connect-time fanout of unrelated events
- **Symptom:** opening a fresh WS receives 13 `task_update`/`session_*` events immediately, before sending any action. These are events from OTHER sessions/workspaces emitted in the moments before connect — but every WS in `ws_clients` (server.py:41) receives every `broadcast()` payload (server.py:350-359).
- **Root cause:** there is no per-WS subscription / filtering. `broadcast()` is global fanout. Output for session X is sent to every connected client — costly under multi-client (mobile + desktop) and a privacy concern under multi-user.
- **Verification:** two simultaneous clients both received hook-event-derived messages even though only one client was "viewing" the session.

### BUG 4 — `replay_turns` against unknown session writes `status` broadcasts and stalls
- **Action:** `{"action":"replay_turns","session_id":"bogus-xxx","turns":["hi"]}`.
- **Actual:** server emits `{"type":"status","status":"replaying"}` for the bogus id (server.py:1582), waits up to 90s in the `_replay_turns` loop (server.py:1586-1605) calling `pty_mgr.write` on a non-existent session (silently fails), then emits `status:idle` + `replay_done`. Verified `bogus` broadcasts arrive on every WS.
- **Impact:** hostile/buggy input causes long-lived background coroutine, fakes status to all clients, no validation. server.py:2527 only checks `session_id and turns`, never that the session exists or is alive.

### BUG 5 — Silent failure modes (no errors emitted)
The following all silently no-op without surfacing any error:
- `start_pty` missing `session_id` → server.py:1655-1656 `continue`.
- `input` to a session that's not running → `pty_manager.py:244-247` `s = self._sessions.get(...)` returns None, no error.
- `stop` on unknown id → `pty_manager.py:261-265` no-op.
- `resize` on unknown id → `pty_manager.py:249-252` no-op.
- `broadcast` containing dead session ids: `pty_mgr.write` returns `False` but server.py:2543-2544 ignores the return value, so partial-failure broadcasts look successful.

This makes the protocol hard to debug from a client and lets bugs hide indefinitely.

## Edge case handling

- **`cols`/`rows` = 0** in `start_pty` → clamped by `PTYSession.__init__` to `max(cols,80)`/`max(rows,24)` at pty_manager.py:26-27. OK.
- **`cols`/`rows` = 0** in `resize` (after start) → passes 0 to `struct.pack("HHHH",...)`, which is valid. The PTY is silently resized to 0×0; not crashing but visibly broken.
- **Negative resize** → struct.error → BUG 1.
- **Resize > 65535** → struct.error → BUG 1.
- **Double-stop** of an already-exited session → no extra messages; idempotent. OK (`pty_manager.py:261-265`).
- **1MB input payload** → accepted; not chunked back to the client; not buffered persistently.
- **No WebSocket message ordering guarantees / sequence numbers** — searched server.py for `seq_num`/`sequence`/`message_seq`: none. Output is batched per-session in 16ms windows (server.py:362-379) which preserves intra-session order, but cross-session ordering is wall-clock only.

## Documentation drift (CLAUDE.md "WebSocket Protocol" vs reality)

CLAUDE.md (the "Client → Server Actions" table) lists 6 actions: `start_pty`, `input`, `resize`, `replay_turns`, `broadcast`, `stop`. The server actually supports many more on the same WS:

| Undocumented action | Location | Purpose |
|---|---|---|
| `preview_start` / `preview_input` / `preview_navigate` / `preview_resize` / `preview_claim_driver` / `preview_screenshot` / `preview_stop` | server.py:2560-2683 | Live Playwright preview multi-peer protocol |
| `hello` / `presence_update` | server.py:2686-2748 | Multiplayer presence (peer name/color/viewing_session) |

The CLAUDE.md "Server → Client" list also omits server-pushed types I observed: `presence_snapshot`, `presence_join`, `presence_update`, `presence_leave`, `preview_frame`, `preview_navigated`, `preview_driver_changed`, `preview_started`, `preview_error`, `preview_screenshot`, `preview_driver_denied` (server.py:2575-2683, 2708-2748).

Other minor drifts:
- The doc implies `start_pty` returns a structured ack; in practice the only confirmation is the `status:running` broadcast (server.py:1668-1672 sends only cached-output for already-alive sessions; no ack on cold start). Clients have to infer success from a delayed `status` event.
- No documented heartbeat/ping; aiohttp default WS keepalives are in effect.

## Recommended fixes (priority order)

1. **Clamp resize cols/rows** in `PTYSession.resize` (pty_manager.py:171) to `[1, 9999]` and/or add `struct.error` to the except. Wrap the WS dispatch loop (server.py:1648 onward) in `try/except Exception` that sends `{"type":"error",...}` and continues.
2. **Per-WS auth / session ownership map**: associate each WS with the set of session_ids it's allowed to drive; reject foreign session_ids with an `error`.
3. **Subscription model** for `broadcast()`: only fan out session-scoped events to WS clients that have viewed/started the session (or opted-in via `hello`).
4. **Validate `replay_turns` session existence** (server.py:2527) before scheduling the long-lived coroutine.
5. **Update CLAUDE.md** WS Protocol section to include `preview_*` and `hello`/`presence_update`, and either define an explicit `ack` for `start_pty` or document that callers must wait for `status:running`.
