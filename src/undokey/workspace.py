from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List, Optional

from undokey.git import real_repo_root
from undokey.store import undokey_home


def _config_path(home: Optional[str] = None) -> str:
    return os.path.join(home or undokey_home(), "config.json")


def read_config(home: Optional[str] = None) -> Dict[str, List[str]]:
    try:
        data = json.loads(Path(_config_path(home)).read_text(encoding="utf-8"))
        return {"track": list(data.get("track") or []), "ignore": list(data.get("ignore") or [])}
    except (OSError, json.JSONDecodeError):
        return {"track": [], "ignore": []}


def write_config(cfg: Dict[str, List[str]], home: Optional[str] = None) -> None:
    h = home or undokey_home()
    os.makedirs(h, exist_ok=True)
    Path(_config_path(h)).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")


def _is_inside(child: str, parent: str) -> bool:
    try:
        rel = os.path.relpath(child, parent)
    except ValueError:
        return False
    return rel == "." or (not rel.startswith("..") and not os.path.isabs(rel))


def _builtin_ignores() -> List[str]:
    h = os.path.expanduser("~")
    names = (".codex", ".claude", ".gemini", ".config", ".local", ".cache", ".npm", ".undokey", "Library")
    return [os.path.join(h, d) for d in names]


def _safe_realpath(p: str) -> str:
    try:
        return os.path.realpath(p)
    except OSError:
        return p


def resolve_worktree(cwd: str, home: Optional[str] = None) -> Optional[str]:
    """Decide which work tree (if any) an agent event in `cwd` should record against.

    - inside a git repo -> the repo's top level
    - inside a directory registered with `undokey track` -> that directory
    - otherwise -> None
    """
    if not cwd or not os.path.exists(cwd):
        return None
    try:
        real = os.path.realpath(cwd)
    except OSError:
        return None

    cfg = read_config(home)
    home_dir = os.path.realpath(os.path.expanduser("~"))
    ignores = [_safe_realpath(p) for p in (*_builtin_ignores(), *cfg["ignore"])]
    if any(_is_inside(real, p) for p in ignores):
        return None

    root = real_repo_root(real)
    if not root:
        tracked = []
        for p in cfg["track"]:
            if os.path.exists(p):
                rp = _safe_realpath(p)
                if _is_inside(real, rp):
                    tracked.append(rp)
        tracked.sort(key=len, reverse=True)
        root = tracked[0] if tracked else None

    if not root:
        return None
    root = os.path.realpath(root)
    if root == home_dir or root == Path(root).anchor:
        return None
    return root
