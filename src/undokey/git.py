from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

_STRIP_GIT_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_PREFIX", "GIT_COMMON_DIR")


@dataclass
class GitResult:
    code: int
    stdout: str
    stderr: str


class GitError(RuntimeError):
    def __init__(self, args: Sequence[str], result: GitResult) -> None:
        super().__init__(f"git {' '.join(args)} failed ({result.code}): {result.stderr.strip()}")
        self.result = result


def git(
    args: Sequence[str],
    *,
    env: Optional[Mapping[str, Optional[str]]] = None,
    cwd: Optional[str] = None,
    input_data: Optional[str] = None,
    allow_fail: bool = False,
) -> GitResult:
    """Run git synchronously without inheriting parent GIT_* variables."""
    run_env = {k: v for k, v in os.environ.items() if k not in _STRIP_GIT_ENV}
    run_env.update(
        {
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_AUTHOR_NAME": "undokey",
            "GIT_AUTHOR_EMAIL": "undokey@localhost",
            "GIT_COMMITTER_NAME": "undokey",
            "GIT_COMMITTER_EMAIL": "undokey@localhost",
        }
    )
    if env:
        for k, v in env.items():
            if v is None:
                run_env.pop(k, None)
            else:
                run_env[k] = v

    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=run_env,
        input=input_data,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    res = GitResult(code=proc.returncode, stdout=proc.stdout or "", stderr=proc.stderr or "")
    if res.code != 0 and not allow_fail:
        raise GitError(args, res)
    return res


def real_repo_root(directory: str) -> Optional[str]:
    """Resolve the top-level of the real git work tree containing `directory`, or None."""
    r = git(["rev-parse", "--show-toplevel"], cwd=directory, allow_fail=True)
    if r.code != 0:
        return None
    out = r.stdout.strip()
    return out or None
