from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List


def turns_from_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Group recorded hook events into agent turns."""
    turns: Dict[str, Dict[str, Any]] = {}
    last_turn: Dict[str, str] = {}
    for ev0 in events:
        sess_key = f"{ev0.get('agent', '')}\x00{ev0.get('session', '')}"
        turn_id = ev0.get("turn") or ""
        if not turn_id and ev0.get("kind") == "turn-start":
            turn_id = f"t{ev0.get('ts', 0)}"
        if not turn_id:
            turn_id = last_turn.get(sess_key, "")
        if not turn_id:
            continue
        if ev0.get("kind") == "turn-start" or ev0.get("turn"):
            last_turn[sess_key] = turn_id

        key = f"{ev0.get('agent', '')}\x00{ev0.get('session', '')}\x00{turn_id}"
        t = turns.get(key)
        if not t:
            t = {
                "agent": ev0.get("agent", ""),
                "session": ev0.get("session", ""),
                "turn": turn_id,
                "startedAt": ev0.get("ts", 0),
                "endedAt": ev0.get("ts", 0),
                "before": None,
                "after": None,
                "prompt": "",
                "transcript": ev0.get("transcript") or "",
                "completed": False,
                "tools": 0,
                "synthetic": not bool(ev0.get("turn")),
            }
            turns[key] = t

        t["endedAt"] = ev0.get("ts", t["endedAt"])
        if ev0.get("transcript"):
            t["transcript"] = ev0["transcript"]
        if ev0.get("prompt") and not t["prompt"]:
            t["prompt"] = ev0["prompt"]
        if ev0.get("kind") == "turn-start" and not t["before"]:
            t["before"] = ev0.get("commit")
        if not t["before"]:
            t["before"] = ev0.get("commit")
        if ev0.get("kind") == "post-tool":
            t["tools"] += 1
        t["after"] = ev0.get("commit")
        if ev0.get("kind") == "turn-end":
            t["completed"] = True

    ordered = sorted(turns.values(), key=lambda x: x["startedAt"])
    for idx, t in enumerate(ordered, start=1):
        t["index"] = idx
    return ordered


def uuidv7() -> str:
    """RFC 9562 UUIDv7 (time-ordered), the format Codex uses for session ids."""
    b = bytearray(secrets.token_bytes(16))
    ms = int(time.time() * 1000)
    for i in range(6):
        b[i] = (ms >> (8 * (5 - i))) & 0xFF
    b[6] = (b[6] & 0x0F) | 0x70
    b[8] = (b[8] & 0x3F) | 0x80
    h = b.hex()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:]}"


def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H-%M-%S")


def fork_codex_session(rollout_path: str, turn_id: str, where: str) -> Dict[str, Any]:
    """Fork a Codex session so that it ends right before (or after) a given turn.

    The original session file is never modified. Resume with `codex resume <new_id>`.
    """
    if not rollout_path or not os.path.exists(rollout_path):
        raise FileNotFoundError(
            f"Codex session file not found: {rollout_path or '(unknown)'}. Was it archived or deleted?"
        )
    lines = [ln for ln in Path(rollout_path).read_text(encoding="utf-8").splitlines() if ln]
    parsed: List[Dict[str, Any]] = []
    for ln in lines:
        try:
            obj = json.loads(ln)
            parsed.append(obj if isinstance(obj, dict) else {})
        except json.JSONDecodeError:
            parsed.append({})

    meta = parsed[0] if parsed else {}
    payload0 = meta.get("payload") if isinstance(meta.get("payload"), dict) else {}
    old_id = (payload0.get("id") or payload0.get("session_id")) if meta.get("type") == "session_meta" else None
    if not old_id:
        raise ValueError("Unrecognized Codex session format (first line is not session_meta).")

    def ptype(e: Dict[str, Any]) -> Any:
        p = e.get("payload")
        return p.get("type") if isinstance(p, dict) else None

    def pturn(e: Dict[str, Any]) -> Any:
        p = e.get("payload")
        return p.get("turn_id") if isinstance(p, dict) else None

    if where == "before":
        cut = next((i for i, e in enumerate(parsed) if ptype(e) == "task_started" and pturn(e) == turn_id), -1)
        if cut == -1:
            raise ValueError(f"Turn {turn_id} not found in {os.path.basename(rollout_path)}.")
        if not any(ptype(e) == "task_complete" for e in parsed[:cut]):
            raise ValueError(
                "This is the first turn of the session: nothing to keep. Just start a new Codex session."
            )
    else:
        end = next((i for i, e in enumerate(parsed) if ptype(e) == "task_complete" and pturn(e) == turn_id), -1)
        if end == -1:
            raise ValueError(
                f"Turn {turn_id} did not complete in {os.path.basename(rollout_path)}; cannot rewind to its end."
            )
        cut = end + 1

    new_id = uuidv7()
    kept = [ln.replace(old_id, new_id) for ln in lines[:cut]]
    dst = os.path.join(os.path.dirname(rollout_path), f"rollout-{_ts()}-{new_id}.jsonl")
    Path(dst).write_text("\n".join(kept) + "\n", encoding="utf-8")
    return {"new_id": new_id, "file": dst, "kept_lines": len(kept)}
