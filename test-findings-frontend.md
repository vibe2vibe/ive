# Frontend Test Findings — IVE Web App

Tested against http://localhost:5173 backed by http://localhost:5111 with 104 sessions across 8 workspaces.

## What works

| Feature | Verified how | Notes |
|---|---|---|
| Multi-session tabs | Opened 4 from sidebar, confirmed all 4 [data-tab-id] elements stay mounted | xterm viewports remain in DOM when inactive |
| ⌘1-9 tab switching | ⌘1 / ⌘3 each toggled the `bg-bg-primary` active class on correct tab | ⌘9 with 4 tabs is a safe no-op |
| ⌘W close tab | Tab count decreased 4→3 and active focus moved | Hardcoded handler in useKeyboard.js:175 |
| ⌘D split view | Side-by-side rendering of two terminals confirmed via screenshot | |
| ⌘⇧Enter Broadcast bar | Modal opened pre-selecting all 5 tabs; "Broadcast to 5 sessions" header shown | Did not actually broadcast (per instructions) |
| ⌘\\ sidebar toggle | `<aside>` element disappears from DOM and reappears on second press | |
| ⌘F search | Panel renders, debounced API call to /api/search returns hits, click jumps + opens session as tab | Tab count went 4→5 after click-result |
| ⌘M Mission Control | Grid shows 69 cards (test workspace), matches API count exactly | Status bar: "104 sessions · 11 active" matches API |
| Mission Control card click | Inner button click activates session, closes panel, sets correct active tab | |
| ⌘I Inbox | Modal shows pending items; sidebar badge "2" matches Inbox header "2 items" | |
| ⌘T Agent Tree | Hierarchy renders parent → branch sub-nodes with model badges (5 commanders / 99 workers) | |
| ⌘⇧N Quick Feature | Modal opens with title field + voice button; submitting created task verified via /api/tasks (66→67) | |
| ⌘N New Session | API sessions count went 104→105 after keypress | |
| Settings → General | `auto_session_titles` PUT to /api/settings persists (on→off→on round-trip via API) | |
| Settings → Workspace | Per-workspace name/color/oversight/tester-mode/worktree fields render | |
| Settings → Sound | Toggle clicks mutate localStorage (round-trip works) | |
| Settings → Experimental | Renders feature flags with `enabled` badges matching backend state | |
| Sidebar context menu (right-click) | Native contextmenu event opens panel with Open/Rename/Copy/Export/Re-summarize/Archive/Stop/Delete | |
| Top-bar "switch" | Dropdown opens listing Claude/Gemini variants minus current model | |
| ⌘Y Quick Action Palette | Renders, list of saved prompt actions, search box focused | Note: shortcut is ⌘Y, **not** ⌘⇧Q as CLAUDE.md claims |
| ⌘⇧A Annotate Output | Terminal annotator with line numbers, "Click to select / Shift+click range / Enter to comment" footer | "Composer" send button correctly disabled until selection exists |
| ⌘E Composer | Multi-line textarea with bullet/marker placeholder | |
| Distill (POST) | /api/sessions/{id}/distill returned `{job_id, status: "started"}` for session with content | Async job correctly enqueued |
| Status bar | Shows "ok / 104 sessions / 11 active / inbox 2 / connected / Mystic Fox" — matches API state | |

## Bugs found

### 1. Search panel: `highlightMatch` is a no-op on UI

- **Repro**: ⌘F → type any common substring (e.g. "todo")
- **Expected**: Matched substring is visually highlighted (bold/colored/`<mark>`) in result rows
- **Actual**: Result text is rendered as a plain string with only context-trimming "..."; zero `<mark>`, `<span>`, or background-color emphasis on the matched chars
- **Root cause**: `frontend/src/components/command/SearchPanel.jsx:135-147` — function is named `highlightMatch` but only slices the string and prepends/appends ellipses; it never wraps the matched substring in any markup. Compare to its name and intent (line 115 `{highlightMatch(r.content, query)}`).

### 2. Sidebar context menu does not close on Escape

- **Repro**: Right-click any session in sidebar → press Escape
- **Expected**: Context menu closes (consistent with all other modals/palettes)
- **Actual**: Menu stays open. Only a click anywhere else dismisses it. I verified this by opening Mission Control on top of an already-open ctx menu — both stayed visible simultaneously.
- **Root cause**: `frontend/src/components/layout/Sidebar.jsx:13-17` (SessionContextMenu effect) registers only `window.addEventListener('click', handler)`. No `keydown` listener for Escape.

### 3. Plan Viewer shows "Approve & Auto-accept" / "Approve & Review Edits" buttons even when there are no plans

- **Repro**: Open a session with no plan (e.g. fresh session) → click the "Plan" button in top-right
- **Expected**: When the panel says "No plans found. Enter plan mode in a session to create one.", the approval buttons should be hidden/disabled (there is nothing to approve)
- **Actual**: Both green "Approve & Auto-accept" and "Approve & Review Edits" buttons render at the bottom. Clicking them sends `1` or `2` keystroke to the PTY (via `sendPlanChoice`) regardless — could trigger an unintended menu pick in a session that wasn't asking
- **Root cause**: `frontend/src/components/session/PlanViewer.jsx:641` — guards on `sessionId` only, not on `workspacePlans.length > 0` or `selectedPlan`. The empty-state copy at line 609 makes the buttons especially misleading.

### 4. ⌘⇧L Pipeline Editor not triggered by real keyboard

- **Repro**: Press ⌘⇧L in browser
- **Expected**: Pipeline Editor opens
- **Actual**: Opens with synthetic dispatchEvent(`key: 'l'`) but real browsers send `event.key === 'L'` when Shift is held; the binding never matches
- **Root cause**: `frontend/src/lib/keybindings.js:22` — `{ key: 'l', meta: true, shift: true }` should be `'L'`. Compare with line 25 (mcpServers `'S'`), line 31 (marketplace `'M'`), line 33 (codeReview `'G'`), line 34 (annotate `'A'`), line 35 (quickFeature `'N'`), line 36 (observatory `'O'`) — all properly uppercase. (User said this is known; included for record.)

### 5. CLAUDE.md vs reality: keybinding documentation drift

CLAUDE.md lists shortcuts that don't match `keybindings.js`. This is a doc bug but worth flagging because user-facing help references would be wrong:

| CLAUDE.md says | Actual binding | Source |
|---|---|---|
| ⌘⇧Q Quick Action Palette | ⌘Y | keybindings.js:15 |
| ⌘⇧F Quick Feature | ⌘⇧N | keybindings.js:35 |
| ⌘⇧K Shortcuts panel | ⌘⇧? | keybindings.js:37 |
| Sidebar ctx menu has "Clone" | No clone option | Sidebar.jsx:98-113 |

## Behavior quirks

- **xterm scroll on tab switch**: switching to a tab and back does not preserve user-scrolled position — xterm auto-scrolls to bottom. Probably intentional for live terminals but not "scroll/state preserved" as the test brief implied.
- **Multiple modals can stack**: ⌘M can open while sidebar context menu is open (because Escape doesn't close ctx menu, and ⌘M keybinding fires regardless). Visually messy but no functional break.
- **Top-bar "switch" button** uses native `confirm()` for "Switch to ${label}? The current session will be stopped..." — not a styled modal. Minor UX inconsistency.
- **Quick Feature** has voice input button labelled `voice` (no mic icon shown when collapsed); functional but unobvious.
- **Status bar legend** has shortcut hints like `⌘⇧L pipeline` — but per bug #4 that shortcut doesn't fire from real keyboard.

## Couldn't test (and why)

- **Sub-agent viewer ⌘T → click into transcript**: Walked all 104 sessions via `/api/sessions/:id/subagents`; every response was `[]`. No session in this DB has spawned subagents, so SubagentViewer rendering can't be exercised.
- **Plan Viewer edit/apply**: No session has a plan in the workspace plans list (panel said "No plans found"). Can't exercise the editor write path; only the empty-state was reachable.
- **Drag-drop reorder**: HTML5 drag/drop with `dataTransfer.setData('reorder-from-ws', ...)` — Playwright `browser_drag` requires snapshot refs that change between renders; would need a longer manual session to verify and the underlying `/api/workspaces/order` and `/api/sessions/order` endpoints aren't hit by simple click flows. Drag handler code at Sidebar.jsx:1218-1246 looks correct on inspection.
- **Distill → result appears in UI**: I successfully kicked the job (200 + job_id), but waiting for the LLM-driven async distill to complete and verifying the resulting guideline shows up in the UI requires several minutes and a session under live monitoring; out of scope for an isolated test pass.
- **Broadcast send**: Per instructions, did not actually broadcast a message to live sessions. UI rendering and session-selection chips verified.
- **Account picker**: Top bar didn't expose a visible account picker on the active session (only `switch / pad / stop`). Component file exists (`AccountManager.jsx`) but wasn't reachable from the active session top-bar.
