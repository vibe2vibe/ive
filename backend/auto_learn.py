"""Autolearn — passive lesson extraction from completed sessions.

When a session finishes (clean exit, idle, or analysis complete), Autolearn
distills the transcript with a focused LLM prompt and writes durable insights
into ``memory_entries`` with ``auto=1`` so they land in a review queue rather
than the canonical memory pool. It also asks the skill suggester for the top
few catalog skills that match the session's intent and broadcasts them as a
non-intrusive ``backgroundResults`` WS message.

Design notes:
  • Default OFF. The whole subscriber registration is gated behind
    ``experimental_autolearn`` in ``app_settings``. If the flag is off when
    ``start(app)`` runs, the module starts but does nothing — no background
    work, no event listeners. Users can flip the flag and call ``start``
    again (or restart) to enable.
  • Never silently mutate manual memory. Auto rows always carry ``auto=1``
    and a ``confidence`` score. ``approve(entry_id)`` flips ``auto=0`` to
    promote one to permanent memory; ``reject(entry_id)`` deletes it.
  • Per-session debounce: don't re-learn from the same session within
    ``_DEBOUNCE_SECONDS`` (1h). Tracked in an in-memory dict; survives a
    process lifetime, which is enough to prevent thrash from multiple
    rapid-fire status changes on the same session.
  • LLM calls go through ``llm_router.llm_call_json``. If no CLI is
    installed (router raises) we silently skip — autolearn must never
    crash a session lifecycle path.
  • Quiet by default: only WARNING+ on real failures, INFO once per
    successful learn. No spam on every session event.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Optional

logger = logging.getLogger(__name__)


# ── Tunables ───────────────────────────────────────────────────────────

_DEBOUNCE_SECONDS = 60 * 60                 # don't relearn same session for 1h
_MIN_TURNS = 5                              # need a real conversation
_MAX_ENTRIES_PER_SESSION = 3                # keep autolearn tight
_DEFAULT_CONFIDENCE_THRESHOLD = 0.7
_TRANSCRIPT_MAX_CHARS = 40_000              # bigger truncation room than distill
_VALID_TYPES = {"user", "feedback", "project", "reference"}

# Subscribed event types — pick the canonical "session is wrapped up" signals
# this codebase emits today.  SESSION_STATUS_CHANGED is reserved for the
# eventual hook-driven idle/exited transition (currently emitted by callers
# that flip session status).  SESSION_ANALYZED fires when the Session Advisor
# finishes scoring a session — a natural moment to also extract lessons.
# SESSION_DELETED is a final hook in case the user closes a tab cleanly.
from commander_events import CommanderEvent

_TRIGGER_EVENTS = (
    CommanderEvent.SESSION_STATUS_CHANGED,
    CommanderEvent.SESSION_ANALYZED,
    CommanderEvent.SESSION_DELETED,
)


# ── Module state ───────────────────────────────────────────────────────

_subscribed = False
_last_learn_at: dict[str, float] = {}       # session_id → epoch seconds


# ── Settings helpers ───────────────────────────────────────────────────

async def _get_setting(key: str, default: Optional[str] = None) -> Optional[str]:
    from db import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT value FROM app_settings WHERE key = ?", (key,),
        )
        row = await cur.fetchone()
        return row["value"] if row else default
    finally:
        await db.close()


async def _is_enabled() -> bool:
    val = await _get_setting("experimental_autolearn", "off")
    return (val or "").strip().lower() == "on"


async def _get_confidence_threshold() -> float:
    raw = await _get_setting("autolearn_confidence_threshold")
    if raw is None:
        return _DEFAULT_CONFIDENCE_THRESHOLD
    try:
        return float(raw)
    except (TypeError, ValueError):
        return _DEFAULT_CONFIDENCE_THRESHOLD


# ── Transcript fetch ───────────────────────────────────────────────────

async def _load_session(session_id: str) -> Optional[dict]:
    from db import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            """SELECT s.*, w.path AS workspace_path
               FROM sessions s
               LEFT JOIN workspaces w ON s.workspace_id = w.id
               WHERE s.id = ?""",
            (session_id,),
        )
        row = await cur.fetchone()
        return dict(row) if row else None
    finally:
        await db.close()


async def _load_messages(session_id: str, sess: dict) -> list[dict]:
    """Pull conversation messages — JSONL first (richer), then DB fallback."""
    messages: list[dict] = []
    native_sid = sess.get("native_session_id")
    workspace_path = sess.get("workspace_path")
    if native_sid and workspace_path:
        try:
            from history_reader import read_session_messages
            from pathlib import Path
            # Mirror server.py's _get_project_dir convention: dashes for slashes.
            dir_name = "-" + workspace_path.lstrip("/").replace("/", "-")
            from config import CLAUDE_HOME
            jsonl = CLAUDE_HOME / "projects" / dir_name / f"{native_sid}.jsonl"
            if jsonl.exists():
                messages = read_session_messages(str(jsonl))
        except Exception as e:
            logger.debug("autolearn: jsonl read failed: %s", e)

    if messages:
        return messages

    from db import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]
    finally:
        await db.close()


def _format_transcript(messages: list[dict]) -> str:
    """Reuse server.py's distill formatter when available; else minimal fallback."""
    try:
        from server import _format_conversation_for_distill
        return _format_conversation_for_distill(messages, max_chars=_TRANSCRIPT_MAX_CHARS)
    except Exception:
        # Avoid hard dependency on server import (circular at startup).
        lines = []
        for m in messages:
            role = m.get("role") or m.get("type") or "?"
            content = m.get("content") or ""
            if isinstance(content, list):
                content = "\n".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if not content:
                continue
            label = "User" if role in ("user", "human") else "Assistant"
            lines.append(f"**{label}:**\n{str(content).strip()}\n")
        text = "\n".join(lines)
        if len(text) > _TRANSCRIPT_MAX_CHARS:
            half = _TRANSCRIPT_MAX_CHARS // 2
            text = text[:half] + "\n\n[...truncated...]\n\n" + text[-half:]
        return text


# ── LLM extraction ─────────────────────────────────────────────────────

_AUTOLEARN_PROMPT = (
    "You are reviewing an agent session transcript to extract durable, "
    "non-obvious insights worth remembering across future sessions.\n\n"
    "Read the transcript and return a JSON array of up to {max_entries} entries. "
    "Each entry must look like:\n"
    "{{\n"
    '  "type": "user" | "feedback" | "project" | "reference",\n'
    '  "name": "<short title, 5-10 words>",\n'
    '  "content": "<2-4 sentences of durable insight>",\n'
    '  "confidence": <number between 0 and 1>\n'
    "}}\n\n"
    "Rules:\n"
    "- SKIP ephemeral task state, debugging steps, in-progress work, error "
    "tracebacks, file paths that don't matter cross-session.\n"
    "- SAVE durable facts about the user (preferences, role, workflow), the "
    "project (architecture, conventions, gotchas), or external resources (URLs, "
    "libraries to remember).\n"
    "- Use type='user' for user preferences/role, 'feedback' for corrections "
    "the user made that should change future behavior, 'project' for codebase "
    "facts, 'reference' for external pointers.\n"
    "- Set confidence high (>0.8) only when the insight is clearly durable and "
    "specific. Lower (<0.6) for borderline observations.\n"
    "- Return [] if nothing is durable enough to save.\n"
    "- Return ONLY the JSON array. No prose, no markdown fences.\n\n"
    "---\n\n"
    "Transcript:\n\n{transcript}"
)


async def _extract_lessons(transcript: str, cli: str) -> list[dict]:
    """Call the LLM to produce candidate memory entries.  Returns [] on any failure."""
    from llm_router import llm_call_json

    prompt = _AUTOLEARN_PROMPT.format(
        max_entries=_MAX_ENTRIES_PER_SESSION,
        transcript=transcript,
    )

    try:
        # llm_call_json returns dict; we asked for an array → it may surface
        # as either a list or a {"entries": [...]} wrapper depending on CLI.
        result = await llm_call_json(cli=cli, prompt=prompt, timeout=120)
    except Exception as e:
        logger.debug("autolearn: llm extraction failed: %s", e)
        return []

    if isinstance(result, list):
        candidates = result
    elif isinstance(result, dict):
        for key in ("entries", "lessons", "results", "items"):
            if isinstance(result.get(key), list):
                candidates = result[key]
                break
        else:
            return []
    else:
        return []

    cleaned: list[dict] = []
    for c in candidates[: _MAX_ENTRIES_PER_SESSION * 2]:
        if not isinstance(c, dict):
            continue
        ctype = (c.get("type") or "").strip().lower()
        if ctype not in _VALID_TYPES:
            continue
        name = (c.get("name") or "").strip()
        content = (c.get("content") or "").strip()
        if not name or not content:
            continue
        try:
            conf = float(c.get("confidence", 0))
        except (TypeError, ValueError):
            conf = 0.0
        conf = max(0.0, min(1.0, conf))
        cleaned.append({
            "type": ctype,
            "name": name[:200],
            "content": content[:2000],
            "confidence": conf,
        })
    return cleaned[:_MAX_ENTRIES_PER_SESSION]


# ── Persistence ────────────────────────────────────────────────────────

async def _save_entry(
    *,
    workspace_id: Optional[str],
    name: str,
    type_: str,
    content: str,
    confidence: float,
    source_cli: str,
    session_id: str,
) -> str:
    """Insert an autolearn row directly so we control auto/confidence flags.

    We don't go through ``memory_manager.save`` because it doesn't expose the
    new ``auto`` / ``confidence`` columns yet.  Schema-compatible row.
    """
    from db import get_db
    entry_id = str(uuid.uuid4())
    db = await get_db()
    try:
        await db.execute(
            """INSERT INTO memory_entries
               (id, workspace_id, name, type, description, content,
                source_cli, tags, auto, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (
                entry_id,
                workspace_id,
                name,
                type_,
                f"autolearn from session {session_id[:8]}",
                content,
                source_cli,
                json.dumps(["autolearn", f"session:{session_id[:8]}"]),
                confidence,
            ),
        )
        await db.commit()
    finally:
        await db.close()
    return entry_id


# ── Skill suggestions ──────────────────────────────────────────────────

async def _suggest_skills(transcript: str, session_id: str, workspace_id: Optional[str]):
    """Match transcript intent against the skill catalog and broadcast top-3."""
    try:
        from skill_suggester import search_skills
    except Exception:
        return

    # Use the head of the transcript as intent context — the user's first few
    # messages capture intent better than the agent's chatter.
    intent = transcript[:4000]
    try:
        results = await search_skills(intent, limit=3, min_score=0.5)
    except Exception as e:
        logger.debug("autolearn: skill_suggester failed: %s", e)
        return

    if not results:
        return

    # Broadcast as a backgroundResults-style message — non-intrusive.
    try:
        from server import broadcast
        await broadcast({
            "type": "backgroundResults",
            "channel": "autolearn",
            "session_id": session_id,
            "workspace_id": workspace_id,
            "skills": results,
        })
    except Exception as e:
        logger.debug("autolearn: broadcast skill suggestions failed: %s", e)


# ── Main learning routine ──────────────────────────────────────────────

async def _learn_from_session(session_id: str) -> None:
    """Full pipeline for one session.  Quiet on failure, INFO on success."""
    if not session_id:
        return

    # Debounce: skip if we processed this session recently.
    now = time.time()
    last = _last_learn_at.get(session_id, 0.0)
    if now - last < _DEBOUNCE_SECONDS:
        return
    _last_learn_at[session_id] = now

    try:
        sess = await _load_session(session_id)
        if not sess:
            return

        # Skip orchestrator sessions — they're meta and produce noise.
        if sess.get("session_type") in ("commander", "tester", "documentor"):
            return

        messages = await _load_messages(session_id, sess)
        if len(messages) < _MIN_TURNS:
            return

        transcript = _format_transcript(messages)
        if not transcript or len(transcript.strip()) < 200:
            return

        cli = sess.get("cli_type") or "claude"
        threshold = await _get_confidence_threshold()

        candidates = await _extract_lessons(transcript, cli)
        kept = [c for c in candidates if c["confidence"] >= threshold]

        saved_ids: list[str] = []
        for c in kept:
            try:
                eid = await _save_entry(
                    workspace_id=sess.get("workspace_id"),
                    name=c["name"],
                    type_=c["type"],
                    content=c["content"],
                    confidence=c["confidence"],
                    source_cli=cli,
                    session_id=session_id,
                )
                saved_ids.append(eid)
            except Exception as e:
                logger.warning("autolearn: failed to persist entry: %s", e)

        if saved_ids:
            logger.info(
                "autolearn: saved %d entries from session %s (cli=%s)",
                len(saved_ids), session_id[:8], cli,
            )

        # Skill suggestions run independently of memory extraction.
        await _suggest_skills(transcript, session_id, sess.get("workspace_id"))

    except Exception:
        # Never let autolearn raise into a hot path.
        logger.exception("autolearn: unexpected failure for session %s", session_id[:8])


# ── Event subscriber ───────────────────────────────────────────────────

async def _on_session_event(event_name: str, payload: dict) -> None:
    """Bus subscriber.  Schedules learning as a background task and returns."""
    session_id = payload.get("session_id")
    if not session_id:
        return

    # Re-check the flag on every event so toggling at runtime takes effect
    # without a restart.  Cheap (one indexed SQLite lookup).
    if not await _is_enabled():
        return

    asyncio.create_task(_learn_from_session(session_id))


# ── Public API (used by server.py routes) ──────────────────────────────

async def list_pending(workspace_id: Optional[str] = None) -> list[dict]:
    """Return all auto-generated memory rows awaiting review."""
    from db import get_db
    sql = "SELECT * FROM memory_entries WHERE auto = 1"
    params: list = []
    if workspace_id:
        sql += " AND (workspace_id = ? OR workspace_id IS NULL)"
        params.append(workspace_id)
    sql += " ORDER BY created_at DESC LIMIT 200"

    db = await get_db()
    try:
        cur = await db.execute(sql, params)
        rows = await cur.fetchall()
        out: list[dict] = []
        for r in rows:
            d = dict(r)
            if isinstance(d.get("tags"), str):
                try:
                    d["tags"] = json.loads(d["tags"])
                except (json.JSONDecodeError, TypeError):
                    d["tags"] = []
            out.append(d)
        return out
    finally:
        await db.close()


async def approve(entry_id: str) -> Optional[dict]:
    """Promote an autolearn row to permanent memory (auto=0)."""
    from db import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            "UPDATE memory_entries SET auto = 0, updated_at = datetime('now') "
            "WHERE id = ? AND auto = 1",
            (entry_id,),
        )
        await db.commit()
        if cur.rowcount == 0:
            return None
        cur2 = await db.execute(
            "SELECT * FROM memory_entries WHERE id = ?", (entry_id,),
        )
        row = await cur2.fetchone()
        if not row:
            return None
        d = dict(row)
        if isinstance(d.get("tags"), str):
            try:
                d["tags"] = json.loads(d["tags"])
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
        return d
    finally:
        await db.close()


async def reject(entry_id: str) -> bool:
    """Delete an autolearn row outright."""
    from db import get_db
    db = await get_db()
    try:
        cur = await db.execute(
            "DELETE FROM memory_entries WHERE id = ? AND auto = 1",
            (entry_id,),
        )
        await db.commit()
        return cur.rowcount > 0
    finally:
        await db.close()


# ── Lifecycle (call from server.py on_startup / on_cleanup) ────────────

async def start(app) -> None:
    """Register subscribers if the experimental flag is on.

    Idempotent — safe to call from on_startup repeatedly.  When the flag is
    off the module stays loaded but does nothing; flip the flag and call
    start again (or restart the server) to enable.
    """
    global _subscribed

    if _subscribed:
        return

    if not await _is_enabled():
        logger.debug("autolearn: experimental flag off — staying dormant")
        return

    from event_bus import bus
    for ev in _TRIGGER_EVENTS:
        bus.subscribe(ev, _on_session_event)
    _subscribed = True
    logger.info("autolearn: subscribed to %d session events", len(_TRIGGER_EVENTS))


async def stop(app) -> None:
    """Unregister subscribers on shutdown."""
    global _subscribed
    if not _subscribed:
        return
    try:
        from event_bus import bus
        for ev in _TRIGGER_EVENTS:
            bus.unsubscribe(ev, _on_session_event)
    except Exception:
        pass
    _subscribed = False
    _last_learn_at.clear()
