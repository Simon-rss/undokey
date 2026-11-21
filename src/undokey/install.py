from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

AGENTS = ("codex", "claude", "opencode", "antigravity")
_MARK = re.compile(r"""undokey(?:\.exe)?["']?\s+hook\s+""")


def _quote(s: str) -> str:
    if os.name == "nt":
        return f'"{s}"' if any(c in s for c in ' \t"') else s
    return shlex.quote(s)


def hook_command(agent: str) -> str:
    """Stable absolute command line for invoking `undokey hook <agent>` from any shell."""
    # Prefer the installed console script next to this Python interpreter (works for uv/pipx/venv).
    exe_dir = Path(os.path.abspath(sys.executable)).parent
    for name in ("undokey.exe", "undokey") if os.name == "nt" else ("undokey",):
        cand = exe_dir / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return f"{_quote(str(cand))} hook {agent}"

    # Fallback: `python -m undokey hook <agent>`. Keep abspath (not realpath) so venv stays intact.
    py = os.path.abspath(sys.executable)
    pkg_parent = str(Path(__file__).resolve().parent.parent)
    # If running directly from an uninstalled source checkout (e.g. PYTHONPATH=src), pass sys.path explicitly.
    if not any(p.endswith("site-packages") or p.endswith("dist-packages") for p in (pkg_parent,)):
        if os.path.basename(pkg_parent) == "src":
            return f"PYTHONPATH={_quote(pkg_parent)} {_quote(py)} -m undokey hook {agent}"
    return f"{_quote(py)} -m undokey hook {agent}"


def detect_agents() -> Dict[str, bool]:
    h = os.path.expanduser("~")
    return {
        "codex": os.path.exists(os.path.join(h, ".codex")) or shutil.which("codex") is not None,
        "claude": os.path.exists(os.path.join(h, ".claude")) or shutil.which("claude") is not None,
        "opencode": os.path.exists(os.path.join(h, ".config", "opencode")) or shutil.which("opencode") is not None,
        "antigravity": os.path.exists(os.path.join(h, ".gemini", "config")) or shutil.which("agy") is not None,
    }


def target_file(agent: str, scope: str, project_root: Optional[str] = None) -> str:
    h = os.path.expanduser("~")
    g = scope == "global"
    if not g and not project_root:
        raise ValueError("project_root is required for project scope")
    base = h if g else str(project_root)
    if agent == "codex":
        return os.path.join(base, ".codex", "hooks.json")
    if agent == "claude":
        return os.path.join(base, ".claude", "settings.json" if g else "settings.local.json")
    if agent == "antigravity":
        return os.path.join(base, ".gemini", "config", "hooks.json") if g else os.path.join(base, ".agents", "hooks.json")
    if agent == "opencode":
        if g:
            return os.path.join(h, ".config", "opencode", "plugin", "undokey.js")
        return os.path.join(base, ".opencode", "plugin", "undokey.js")
    raise ValueError(f"unknown agent {agent}")


def _read_json(file: str) -> Dict[str, Any]:
    if not os.path.exists(file):
        return {}
    text = Path(file).read_text(encoding="utf-8")
    if not text.strip():
        return {}
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"{file} is not valid JSON; not touching it ({e})") from e
    if not isinstance(obj, dict):
        raise ValueError(f"{file} must contain a JSON object; not touching it")
    return obj


def _write_with_backup(file: str, content: str) -> Dict[str, Any]:
    path = Path(file)
    old = path.read_text(encoding="utf-8") if path.exists() else None
    if old == content:
        return {"changed": False, "backup": None}
    path.parent.mkdir(parents=True, exist_ok=True)
    backup: Optional[str] = None
    if old is not None:
        backup = f"{file}.undokey-{int(time.time() * 1000)}.bak"
        Path(backup).write_text(old, encoding="utf-8")
    path.write_text(content, encoding="utf-8")
    return {"changed": True, "backup": backup}


def _is_ours(group: Any) -> bool:
    if not isinstance(group, dict):
        return False
    handlers = group.get("hooks") if isinstance(group.get("hooks"), list) else [group]
    return any(isinstance(h, dict) and isinstance(h.get("command"), str) and _MARK.search(h["command"]) for h in handlers)


def _strip_events(events: Dict[str, Any]) -> None:
    for k in list(events.keys()):
        v = events[k]
        if not isinstance(v, list):
            continue
        events[k] = [g for g in v if not _is_ours(g)]
        if not events[k]:
            del events[k]


def _claude_style_groups(agent: str, matcher_all: str) -> Dict[str, Any]:
    handler = {"type": "command", "command": hook_command(agent), "timeout": 30}
    return {
        "UserPromptSubmit": [{"hooks": [handler]}],
        "PreToolUse": [{"matcher": matcher_all, "hooks": [handler]}],
        "PostToolUse": [{"matcher": matcher_all, "hooks": [handler]}],
        "Stop": [{"hooks": [handler]}],
    }


def _opencode_plugin() -> str:
    cmd = json.dumps(hook_command("opencode"))
    return f"""// Generated by undokey (https://github.com/Simon-rss/undokey). Remove with: undokey uninstall --agent opencode
import {{ spawnSync }} from "node:child_process";
const CMD = {cmd};
const fire = (payload) => {{
  try {{
    spawnSync(CMD, {{ shell: true, input: JSON.stringify(payload), timeout: 30000, stdio: ["pipe", "ignore", "ignore"] }});
  }} catch {{}}
}};
export const Undokey = async ({{ directory }}) => ({{
  "chat.message": async (input) => {{
    fire({{ hook_event_name: "UserPromptSubmit", session_id: input?.sessionID, turn_id: input?.messageID, cwd: directory }});
  }},
  "tool.execute.before": async (input) => {{
    fire({{ hook_event_name: "PreToolUse", tool_name: input?.tool, session_id: input?.sessionID, tool_use_id: input?.callID, cwd: directory }});
  }},
  "tool.execute.after": async (input) => {{
    fire({{ hook_event_name: "PostToolUse", tool_name: input?.tool, session_id: input?.sessionID, tool_use_id: input?.callID, cwd: directory }});
  }},
  event: async ({{ event }}) => {{
    if (event?.type === "session.idle") fire({{ hook_event_name: "Stop", session_id: event?.properties?.sessionID, cwd: directory }});
  }},
}});
"""


def install(agent: str, scope: str, project_root: Optional[str] = None) -> Dict[str, Any]:
    file = target_file(agent, scope, project_root)
    if agent == "opencode":
        return {"agent": agent, "file": file, **_write_with_backup(file, _opencode_plugin())}

    cfg = _read_json(file)
    if agent == "antigravity":
        cmd = hook_command(agent)
        cfg["undokey"] = {
            "PreInvocation": [{"type": "command", "command": cmd, "timeout": 30}],
            "PostToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command": cmd, "timeout": 30}]}],
            "Stop": [{"type": "command", "command": cmd, "timeout": 30}],
        }
        return {"agent": agent, "file": file, **_write_with_backup(file, json.dumps(cfg, indent=2) + "\n")}

    # codex: hooks.json is either { hooks: { Event: [...] } } or { Event: [...] }
    # claude: settings.json with { hooks: { Event: [...] } }
    if agent == "claude" or isinstance(cfg.get("hooks"), dict) or not cfg:
        if not isinstance(cfg.get("hooks"), dict):
            cfg["hooks"] = {}
        events = cfg["hooks"]
    else:
        events = cfg

    _strip_events(events)
    add = _claude_style_groups(agent, "*" if agent == "claude" else ".*")
    for k, groups in add.items():
        events[k] = [*(events.get(k) or []), *groups]

    res = _write_with_backup(file, json.dumps(cfg, indent=2) + "\n")
    note = (
        "Codex asks you to trust new hooks once: open `codex`, run /hooks and approve the undokey entries."
        if agent == "codex" and res["changed"]
        else None
    )
    return {"agent": agent, "file": file, **res, "note": note}


def uninstall(agent: str, scope: str, project_root: Optional[str] = None) -> Dict[str, Any]:
    file = target_file(agent, scope, project_root)
    if not os.path.exists(file):
        return {"agent": agent, "file": file, "changed": False, "backup": None}
    if agent == "opencode":
        if "Generated by undokey" not in Path(file).read_text(encoding="utf-8"):
            return {"agent": agent, "file": file, "changed": False, "backup": None}
        os.remove(file)
        return {"agent": agent, "file": file, "changed": True, "backup": None}

    cfg = _read_json(file)
    if agent == "antigravity":
        if "undokey" not in cfg:
            return {"agent": agent, "file": file, "changed": False, "backup": None}
        del cfg["undokey"]
    else:
        events = cfg["hooks"] if isinstance(cfg.get("hooks"), dict) else cfg
        _strip_events(events)
        if isinstance(cfg.get("hooks"), dict) and not cfg["hooks"]:
            del cfg["hooks"]
    return {"agent": agent, "file": file, **_write_with_backup(file, json.dumps(cfg, indent=2) + "\n")}


def is_installed(agent: str, scope: str, project_root: Optional[str] = None) -> bool:
    file = target_file(agent, scope, project_root)
    if not os.path.exists(file):
        return False
    text = Path(file).read_text(encoding="utf-8")
    return "Generated by undokey" in text if agent == "opencode" else bool(_MARK.search(text))
