from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, TypeVar

from undokey.git import GitResult, git, real_repo_root

TIMELINE_REF = "refs/heads/timeline"
_META_PREFIX = "undokey: "
_STAT_RE = re.compile(
    r"(\d+) files? changed(?:, (\d+) insertions?\(\+\))?(?:, (\d+) deletions?\(-\))?"
)

T = TypeVar("T")


def undokey_home() -> str:
    return os.environ.get("UNDOKEY_HOME") or os.path.join(os.path.expanduser("~"), ".undokey")


def key_for(worktree: str) -> str:
    return hashlib.sha1(worktree.encode("utf-8")).hexdigest()[:16]


@dataclass
class Checkpoint:
    id: str
    parent: Optional[str]
    time: datetime
    summary: str
    meta: Dict[str, Any]
    files: int = 0
    insertions: int = 0
    deletions: int = 0


class Store:
    """A shadow bare git repository recording snapshots of one work tree.

    Never touches the work tree's own .git directory or index.
    """

    def __init__(self, worktree: str, home: Optional[str] = None) -> None:
        self.worktree = os.path.realpath(worktree)
        self.home = home or undokey_home()
        key = key_for(self.worktree)
        self.dir = os.path.join(self.home, "repos", f"{key}.git")
        self.lock_dir = os.path.join(self.home, "repos", f"{key}.lock")
        self.events_file = os.path.join(self.dir, "undokey-events.jsonl")
        self.env: Dict[str, str] = {
            "GIT_DIR": self.dir,
            "GIT_WORK_TREE": self.worktree,
            "GIT_INDEX_FILE": os.path.join(self.dir, "undokey-index"),
        }

    def exists(self) -> bool:
        return os.path.exists(os.path.join(self.dir, "HEAD"))

    def g(
        self,
        args: Sequence[str],
        *,
        env: Optional[Dict[str, Optional[str]]] = None,
        input_data: Optional[str] = None,
        allow_fail: bool = False,
    ) -> GitResult:
        merged: Dict[str, Optional[str]] = dict(self.env)
        if env:
            merged.update(env)
        return git(args, env=merged, cwd=self.worktree, input_data=input_data, allow_fail=allow_fail)

    def init(self) -> None:
        if self.exists():
            return
        os.makedirs(os.path.dirname(self.dir), exist_ok=True)
        git(["init", "-q", "--bare", self.dir])
        for k, v in (("core.autocrlf", "false"), ("core.quotepath", "false"), ("gc.auto", "0")):
            git(["--git-dir", self.dir, "config", k, v])
        meta_path = os.path.join(self.dir, "undokey-meta.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "worktree": self.worktree,
                    "createdAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
                f,
                indent=2,
            )

    def with_lock(self, fn: Callable[[], T], timeout_ms: int = 15000) -> T:
        """Serialize access: agents fire hooks concurrently for parallel tool calls."""
        os.makedirs(os.path.dirname(self.lock_dir), exist_ok=True)
        deadline = time.monotonic() + timeout_ms / 1000.0
        while True:
            try:
                os.mkdir(self.lock_dir)
                break
            except FileExistsError:
                try:
                    age = time.time() - os.stat(self.lock_dir).st_mtime
                    if age > 120:
                        os.rmdir(self.lock_dir)  # stale lock from a killed process
                except OSError:
                    pass
                if time.monotonic() > deadline:
                    raise TimeoutError(f"undokey: timed out waiting for lock {self.lock_dir}")
                time.sleep(0.025)
        try:
            return fn()
        finally:
            try:
                os.rmdir(self.lock_dir)
            except OSError:
                pass

    def head(self) -> Optional[str]:
        r = self.g(["rev-parse", "-q", "--verify", TIMELINE_REF], allow_fail=True)
        return r.stdout.strip() if r.code == 0 else None

    def sync_excludes(self) -> None:
        """Copy the real repo's info/exclude so locally-excluded files are not snapshotted."""
        if not real_repo_root(self.worktree):
            return
        r = git(["rev-parse", "--git-path", "info/exclude"], cwd=self.worktree, allow_fail=True)
        if r.code != 0:
            return
        src = Path(self.worktree) / r.stdout.strip()
        dst = Path(self.dir) / "info" / "exclude"
        try:
            content = src.read_text(encoding="utf-8")
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.read_text(encoding="utf-8") != content:
                dst.write_text(content, encoding="utf-8")
        except OSError:
            pass

    def stage(self) -> str:
        """Stage the current work tree into the shadow index and return the tree id."""
        self.sync_excludes()
        self.g(["add", "-A", "--ignore-errors", "."], allow_fail=True)
        # Files tracked by the real repo but matching .gitignore (force-added) must be kept too.
        if real_repo_root(self.worktree):
            ls = git(["ls-files", "-z", "-c", "-i", "--exclude-standard"], cwd=self.worktree, allow_fail=True)
            if ls.code == 0 and ls.stdout:
                self.g(
                    ["add", "-f", "--ignore-errors", "--pathspec-from-file=-", "--pathspec-file-nul"],
                    input_data=ls.stdout,
                    allow_fail=True,
                )
        return self.g(["write-tree"]).stdout.strip()

    def snapshot(self, summary: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record the work tree. Caller must hold the lock (use snapshot_locked otherwise)."""
        self.init()
        tree = self.stage()
        parent = self.head()
        meta_copy = dict(meta or {})
        if parent:
            ptree = self.g(["rev-parse", f"{parent}^{{tree}}"]).stdout.strip()
            if ptree == tree:
                return {"commit": parent, "changed": False}
        else:
            summary = "baseline (first checkpoint)"
            meta_copy["kind"] = "baseline"

        payload = {"ts": int(time.time() * 1000), **meta_copy}
        body = f"{summary}\n\n{_META_PREFIX}{json.dumps(payload, ensure_ascii=False)}\n"
        args = ["commit-tree", tree]
        if parent:
            args.extend(["-p", parent])
        commit = self.g(args, input_data=body).stdout.strip()
        ref_args = ["update-ref", TIMELINE_REF, commit]
        if parent:
            ref_args.append(parent)
        self.g(ref_args)
        return {"commit": commit, "changed": True}

    def snapshot_locked(self, summary: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return self.with_lock(lambda: self.snapshot(summary, meta))

    def resolve(self, rev: str) -> str:
        r = self.g(["rev-parse", "-q", "--verify", f"{rev}^{{commit}}"], allow_fail=True)
        if r.code != 0:
            raise ValueError(f'undokey: unknown checkpoint "{rev}"')
        return r.stdout.strip()

    def restore(self, rev: str, meta: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
        """Make the work tree exactly match a checkpoint. Current state is saved first."""

        def _do() -> Dict[str, str]:
            self.init()
            target = self.resolve(rev)
            before = self.snapshot(f"before restore to {target[:8]}", {"kind": "pre-restore", "target": target})
            self.g(["read-tree", "-u", "--reset", target])
            after = self.snapshot(
                f"restored to {target[:8]}",
                {"kind": "restore", "target": target, **(meta or {})},
            )
            return {"target": target, "before": before["commit"], "after": after["commit"]}

        return self.with_lock(_do)

    @staticmethod
    def _parse_message(message: str) -> tuple[str, Dict[str, Any]]:
        lines = message.strip().split("\n")
        summary = lines[0] if lines else ""
        meta: Dict[str, Any] = {}
        for line in lines:
            if line.startswith(_META_PREFIX):
                try:
                    meta = json.loads(line[len(_META_PREFIX) :])
                except json.JSONDecodeError:
                    meta = {}
                break
        return summary, meta

    def list(self, limit: int = 30) -> List[Checkpoint]:
        if not self.head():
            return []
        out = self.g(
            [
                "log",
                f"-n{limit}",
                "--first-parent",
                "--format=%x1e%H%x1f%P%x1f%ct%x1f%B%x1f",
                "--shortstat",
                TIMELINE_REF,
            ]
        ).stdout
        entries: List[Checkpoint] = []
        for rec in out.split("\x1e"):
            if not rec.strip():
                continue
            parts = rec.split("\x1f")
            cid, parents, ct, message = parts[0], parts[1], parts[2], parts[3]
            stat = parts[4] if len(parts) > 4 else ""
            summary, meta = self._parse_message(message)
            m = _STAT_RE.search(stat)
            entries.append(
                Checkpoint(
                    id=cid,
                    parent=parents.strip().split(" ")[0] or None,
                    time=datetime.fromtimestamp(int(ct)),
                    summary=summary,
                    meta=meta,
                    files=int(m.group(1)) if m else 0,
                    insertions=int(m.group(2)) if m and m.group(2) else 0,
                    deletions=int(m.group(3)) if m and m.group(3) else 0,
                )
            )
        return entries

    def entry(self, rev: str) -> Checkpoint:
        cid = self.resolve(rev)
        out = self.g(["log", "-n1", "--format=%P%x1f%ct%x1f%B", cid]).stdout
        parents, ct, message = out.split("\x1f", 2)
        summary, meta = self._parse_message(message)
        return Checkpoint(
            id=cid,
            parent=parents.strip().split(" ")[0] or None,
            time=datetime.fromtimestamp(int(ct)),
            summary=summary,
            meta=meta,
        )

    def diff(self, rev: str, *, against: str = "parent", stat: bool = False, color: bool = False) -> str:
        cid = self.resolve(rev)
        color_flag = "--color=always" if color else "--color=never"
        fmt = ["--stat"] if stat else []
        if against == "now":
            def _now() -> str:
                tree = self.stage()
                return self.g(["diff", color_flag, *fmt, cid, tree]).stdout

            return self.with_lock(_now)
        e = self.entry(cid)
        if not e.parent:
            return self.g(["show", color_flag, *fmt, "--format=", cid]).stdout
        return self.g(["diff", color_flag, *fmt, e.parent, cid]).stdout

    def append_event(self, event: Dict[str, Any]) -> None:
        self.init()
        with open(self.events_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    def read_events(self) -> List[Dict[str, Any]]:
        if not os.path.exists(self.events_file):
            return []
        events: List[Dict[str, Any]] = []
        with open(self.events_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return events

    def disk_bytes(self) -> int:
        """Total bytes used on disk by this shadow repository."""
        if not os.path.exists(self.dir):
            return 0
        total = 0
        for base, _, files in os.walk(self.dir):
            for name in files:
                try:
                    total += os.path.getsize(os.path.join(base, name))
                except OSError:
                    pass
        return total

    def gc(self) -> Dict[str, int]:
        """Pack loose objects to reclaim disk space."""
        def _do() -> Dict[str, int]:
            before = self.disk_bytes()
            if self.exists():
                self.g(["gc", "-q", "--prune=now"])
            after = self.disk_bytes()
            return {"before": before, "after": after}

        return self.with_lock(_do)


def list_stores(home: Optional[str] = None) -> List[Dict[str, str]]:
    h = home or undokey_home()
    repos_dir = os.path.join(h, "repos")
    if not os.path.isdir(repos_dir):
        return []
    out: List[Dict[str, str]] = []
    for name in os.listdir(repos_dir):
        if not name.endswith(".git"):
            continue
        meta_file = os.path.join(repos_dir, name, "undokey-meta.json")
        try:
            meta = json.loads(Path(meta_file).read_text(encoding="utf-8"))
            out.append({"key": name[:-4], "worktree": meta["worktree"], "dir": os.path.join(repos_dir, name)})
        except (OSError, json.JSONDecodeError, KeyError):
            pass
    return out
