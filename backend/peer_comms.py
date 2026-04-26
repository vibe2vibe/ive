"""Peer communication helpers for worker/commander MCP servers.

Provides:
  • `post_peer_message`     — wrap REST POST /workspaces/{id}/peer-messages
                              with the new `blocking` / `in_reply_to` fields.
  • `wait_for_reply`        — synchronous polling until a reply arrives or
                              the timeout elapses. Used by `blocking_bulletin`.
  • `myelin_*`              — thin wrappers around `myelin.coordination` so
                              both MCP servers can expose coord tools without
                              duplicating the import dance. All wrappers
                              fail-soft when myelin isn't installed.

These helpers are intentionally storage-agnostic: they call the local REST
API (which the MCP servers already have configured via COMMANDER_API_URL)
and never touch sqlite directly. Keeping the abstraction at the HTTP layer
means we don't have to thread async DB pools through the MCP stdio loop.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


# ─── REST helper (thin wrapper, mirrors the one in each MCP server) ─────

def _api_call(api_url: str, method: str, path: str, body: dict | None = None) -> Any:
    url = f"{api_url}/api{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json"} if body else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode(), "status": e.code}
    except Exception as e:
        return {"error": str(e)}


# ─── Peer message primitives ────────────────────────────────────────────

def post_peer_message(
    api_url: str,
    workspace_id: str,
    from_session_id: str,
    content: str,
    *,
    to: str = "all",
    topic: str = "general",
    priority: str = "info",
    blocking: bool = False,
    in_reply_to: str | None = None,
    files: list[str] | None = None,
) -> dict:
    """POST a peer message. `to` is encoded into topic for now (recipient
    routing isn't a first-class column — workers filter their bulletin feed
    by topic, so we tag the recipient as `topic=to:<id>` when not 'all').

    Returns the created message row (or {"error": ...}).
    """
    effective_topic = topic
    if to and to != "all":
        # Prefix recipient so the receiving side can filter quickly without
        # needing a new column. Original topic is preserved as a suffix.
        effective_topic = f"to:{to}|{topic}"

    body = {
        "from_session_id": from_session_id,
        "topic": effective_topic,
        "content": content,
        "priority": priority,
        "files": files or [],
        "blocking": 1 if blocking else 0,
    }
    if in_reply_to:
        body["in_reply_to"] = in_reply_to
    return _api_call(api_url, "POST", f"/workspaces/{workspace_id}/peer-messages", body)


def wait_for_reply(
    api_url: str,
    workspace_id: str,
    bulletin_id: str,
    timeout_secs: int = 600,
    poll_interval: float = 2.0,
) -> dict | None:
    """Block until a peer posts a message with `in_reply_to == bulletin_id`,
    or until `timeout_secs` elapses.

    Returns the reply message dict on success, None on timeout.

    Polls the bulletin board endpoint at `poll_interval` seconds. The MCP
    stdio loop is single-threaded so this WILL block the worker — that's
    by design (the agent is supposed to pause).
    """
    deadline = time.time() + max(1, timeout_secs)
    seen_ids: set[str] = set()

    while time.time() < deadline:
        # Fetch recent messages — bulletin endpoint returns newest first.
        msgs = _api_call(api_url, "GET", f"/workspaces/{workspace_id}/peer-messages")
        if isinstance(msgs, list):
            for m in msgs:
                mid = m.get("id")
                if not mid or mid in seen_ids:
                    continue
                seen_ids.add(mid)
                if m.get("in_reply_to") == bulletin_id:
                    return m
        # Sleep before next poll. Keep poll_interval coarse — agents that
        # ask blocking questions are fine waiting a couple seconds.
        time.sleep(poll_interval)

    return None


# ─── Myelin coordination wrappers ───────────────────────────────────────
#
# We import lazily so the worker MCP server starts even when `ext-repo/`
# isn't on the path (e.g. on machines that don't use the experimental
# coordination feature). The first myelin call inserts ext-repo into
# sys.path, then attempts the import; failures degrade to {"available": False}.

_MYELIN_READY: bool | None = None  # tri-state: None=untried, True=loaded, False=failed


def _ensure_myelin_path() -> None:
    """Insert <repo>/ext-repo into sys.path so `from myelin import ...` works."""
    here = Path(__file__).resolve().parent
    repo_root = here.parent
    ext_repo = repo_root / "ext-repo"
    if ext_repo.exists() and str(ext_repo) not in sys.path:
        sys.path.insert(0, str(ext_repo))


def _try_import_myelin() -> bool:
    """Best-effort myelin import. Caches result for the process lifetime."""
    global _MYELIN_READY
    if _MYELIN_READY is not None:
        return _MYELIN_READY
    try:
        _ensure_myelin_path()
        # Smoke import — the public API surfaces we use:
        from myelin.coordination import AgentWorkspace  # noqa: F401
        from myelin import Myelin  # noqa: F401
        _MYELIN_READY = True
    except Exception:
        _MYELIN_READY = False
    return _MYELIN_READY


def _build_workspace():
    """Return an `AgentWorkspace` bound to the shared coord DB.

    Uses the same env vars as `ext-repo/myelin/coordination/hook.py` so the
    MCP-side and hook-side share a graph: MYELIN_DB_PATH, MYELIN_NAMESPACE.
    """
    if not _try_import_myelin():
        return None
    from myelin import Myelin
    from myelin.coordination import AgentWorkspace
    from myelin.storage.sqlite import SQLiteStorage
    from myelin.core.embeddings import GeminiEmbedding

    db_path = os.environ.get("MYELIN_DB_PATH", os.path.expanduser("~/.myelin/coord.db"))
    namespace = os.environ.get("MYELIN_NAMESPACE", "claude_code:shared")
    try:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        storage = SQLiteStorage(db_path=db_path, embedding_dims=3072)
        embedder = GeminiEmbedding()
        brain = Myelin(namespace=namespace, storage=storage, embedder=embedder)
        return AgentWorkspace(brain)
    except Exception:
        return None


def myelin_check_overlap(agent_id: str, intent: str, file_path: str = "") -> dict:
    """Check whether `intent` semantically overlaps any active peer task.

    Returns:
        {"available": False}                        — myelin not installed
        {"available": True, "overlaps": [...]}     — list of {agent_id, intent, score, level}
    """
    import asyncio

    ws = _build_workspace()
    if ws is None:
        return {"available": False, "reason": "myelin not available"}

    async def _run():
        return await ws.check_overlap(intent=intent, exclude_agent=agent_id)

    try:
        tasks = asyncio.run(_run())
    except Exception as e:
        return {"available": False, "reason": str(e)}

    return {
        "available": True,
        "overlaps": [
            {
                "agent_id": t.agent_id,
                "intent": t.intent,
                "score": round(t.score, 3),
                "level": t.level.value if hasattr(t.level, "value") else str(t.level),
                "files_touched": t.files_touched,
                "started_at": t.started_at,
            }
            for t in tasks
        ],
        "file_path": file_path,
    }


def myelin_acquire(agent_id: str, file_path: str, intent: str = "") -> dict:
    """Best-effort claim — announces a task. Not a hard lock; peers can still
    write, but they'll see the announcement on their next overlap check."""
    import asyncio

    ws = _build_workspace()
    if ws is None:
        return {"available": False, "reason": "myelin not available"}

    intent_text = intent or f"editing {file_path}"

    async def _run():
        return await ws.announce(
            agent_id=agent_id,
            intent=intent_text,
            files_touched=[file_path] if file_path else [],
        )

    try:
        task = asyncio.run(_run())
    except Exception as e:
        return {"available": False, "reason": str(e)}

    return {
        "available": True,
        "task_id": task.id,
        "agent_id": task.agent_id,
        "intent": task.intent,
        "files_touched": task.files_touched,
    }


def myelin_release(agent_id: str, file_path: str) -> dict:
    """Mark all of this agent's active tasks for `file_path` as completed.

    Best-effort — relies on the `complete` helper in AgentWorkspace if present;
    otherwise updates the node properties directly.
    """
    import asyncio

    if not _try_import_myelin():
        return {"available": False, "reason": "myelin not available"}

    ws = _build_workspace()
    if ws is None:
        return {"available": False, "reason": "myelin not available"}

    async def _run():
        # Look up active tasks for this agent that mention the file. Use
        # check_overlap with the file path as intent — cheap and good enough.
        intent = file_path or agent_id
        tasks = await ws.check_overlap(intent=intent, threshold=0.0)
        released = 0
        for t in tasks:
            if t.agent_id != agent_id:
                continue
            # Soft completion: try to reach into Myelin to update status.
            try:
                myelin = ws._myelin
                node = await myelin.get_node(t.id)
                if node:
                    props = (node.get("properties") or {}).copy()
                    props["status"] = "completed"
                    await myelin.execute("myelin_update", {"node_id": t.id, "properties": props})
                    released += 1
            except Exception:
                pass
        return released

    try:
        n = asyncio.run(_run())
    except Exception as e:
        return {"available": False, "reason": str(e)}

    return {"available": True, "released": n}


def myelin_peers(agent_id: str) -> dict:
    """List active peer tasks in this workspace's coordination namespace."""
    import asyncio

    ws = _build_workspace()
    if ws is None:
        return {"available": False, "reason": "myelin not available"}

    async def _run():
        # Empty-string intent triggers a near-zero threshold scan returning
        # everything active. We pass a tiny threshold to be defensive.
        return await ws.check_overlap(intent="*", threshold=0.0, exclude_agent=agent_id)

    try:
        tasks = asyncio.run(_run())
    except Exception as e:
        return {"available": False, "reason": str(e)}

    return {
        "available": True,
        "peers": [
            {
                "agent_id": t.agent_id,
                "intent": t.intent,
                "files_touched": t.files_touched,
                "started_at": t.started_at,
            }
            for t in tasks
        ],
    }
