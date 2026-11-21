from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Optional

from undokey.events import hook_stdout, normalize, summarize
from undokey.git import git
from undokey.store import Store, undokey_home
from undokey.workspace import resolve_worktree

INLINE_BASELINE_MAX_FILES = 3000


def _log_error(home: str, msg: str) -> None:
    try:
        os.makedirs(home, exist_ok=True)
        ts = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        with open(os.path.join(home, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"{ts} {msg}\n")
    except OSError:
        pass


def _tracked_file_count(worktree: str) -> int:
    r = git(["ls-files", "-z"], cwd=worktree, allow_fail=True)
    if r.code != 0:
        return 0
    return r.stdout.count("\0")


def handle_hook(agent: str, raw: str, home: Optional[str] = None) -> str:
    """Entry point for `undokey hook <agent>`.

    Must never break the agent: every error is swallowed and logged; process exits 0.
    """
    h = home or undokey_home()
    try:
        payload = json.loads(raw) if raw and raw.strip() else {}
        if not isinstance(payload, dict):
            payload = {}
        e = normalize(agent, payload)
        worktree = resolve_worktree(e.cwd, h)
        if not worktree:
            return hook_stdout(agent)
        store = Store(worktree, h)

        if not store.exists() and _tracked_file_count(worktree) > INLINE_BASELINE_MAX_FILES:
            def _claim() -> bool:
                if store.exists():
                    return False
                store.init()
                return True

            if store.with_lock(_claim):
                env = dict(os.environ, UNDOKEY_HOME=h)
                kwargs: dict = {
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                    "env": env,
                }
                if os.name == "nt":
                    kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                    )
                else:
                    kwargs["start_new_session"] = True
                subprocess.Popen(
                    [sys.executable, "-m", "undokey", "snapshot", worktree, "--label", "baseline", "--quiet"],
                    **kwargs,
                )
                return hook_stdout(agent)

        res = store.snapshot_locked(
            summarize(e),
            {
                "agent": e.agent,
                "event": e.event,
                "kind": e.kind,
                "tool": e.tool,
                "session": e.session,
                "turn": e.turn,
                "toolUseId": e.tool_use_id,
            },
        )
        store.append_event(
            {
                "ts": int(time.time() * 1000),
                "agent": e.agent,
                "event": e.event,
                "kind": e.kind,
                "tool": e.tool,
                "session": e.session,
                "turn": e.turn,
                "toolUseId": e.tool_use_id,
                "transcript": e.transcript,
                "prompt": e.prompt[:200] if e.prompt else "",
                "commit": res["commit"],
                "changed": res["changed"],
            }
        )
    except Exception:
        _log_error(h, f"hook {agent}: {traceback.format_exc().strip()}")
    return hook_stdout(agent)
