from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

SRC_DIR = str(Path(__file__).resolve().parent.parent / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)


def tmpdir(prefix: str = "undokey-test-") -> str:
    return os.path.realpath(tempfile.mkdtemp(prefix=prefix))


def sh(cmd: Sequence[str], *, cwd: Optional[str] = None, env: Optional[Dict[str, str]] = None, input_data: Optional[str] = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        cwd=cwd,
        env=env,
        input=input_data,
        text=True,
        encoding="utf-8",
        capture_output=True,
    )


def write_files(root: str, files: Dict[str, str]) -> None:
    for rel, content in files.items():
        p = Path(root) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")


def make_repo(files: Optional[Dict[str, str]] = None) -> str:
    repo = os.path.join(tmpdir(), "proj")
    os.makedirs(repo, exist_ok=True)
    write_files(repo, files or {"a.txt": "hi\n"})
    sh(["git", "init", "-q"], cwd=repo)
    sh(["git", "add", "-A"], cwd=repo)
    sh(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=repo)
    return repo


def cli(
    args: Sequence[str],
    *,
    home: str,
    cwd: Optional[str] = None,
    input_data: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    env = dict(os.environ)
    env["HOME"] = home
    env["UNDOKEY_HOME"] = os.path.join(home, ".undokey")
    env["NO_COLOR"] = "1"
    env["PYTHONPATH"] = SRC_DIR + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    if extra_env:
        env.update(extra_env)
    r = sh([sys.executable, "-m", "undokey", *args], cwd=cwd, env=env, input_data=input_data)
    return {"code": r.returncode, "out": r.stdout, "err": r.stderr}


def hook(agent: str, payload: Union[str, Dict[str, Any]], *, home: str, cwd: Optional[str] = None) -> Dict[str, Any]:
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    return cli(["hook", agent], home=home, cwd=cwd, input_data=raw)


def list_files(root: str) -> List[str]:
    out: List[str] = []
    for base, dirs, files in os.walk(root):
        if ".git" in dirs:
            dirs.remove(".git")
        for f in files:
            out.append(os.path.relpath(os.path.join(base, f), root))
    return sorted(out)
