from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

from helpers import SRC_DIR, list_files, make_repo, sh, tmpdir, write_files
from undokey.store import Store


def _git_status(repo: str) -> str:
    return sh(["git", "status", "--porcelain"], cwd=repo).stdout


class StoreTests(unittest.TestCase):
    def test_snapshot_only_commits_on_change(self) -> None:
        repo = make_repo()
        s = Store(repo, tmpdir())
        a = s.snapshot_locked("first")
        self.assertTrue(a["changed"])
        b = s.snapshot_locked("again")
        self.assertFalse(b["changed"])
        self.assertEqual(b["commit"], a["commit"])

        write_files(repo, {"a.txt": "changed\n"})
        c = s.snapshot_locked("edit", {"agent": "codex"})
        self.assertTrue(c["changed"])
        entries = s.list()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].summary, "edit")
        self.assertEqual(entries[0].meta["agent"], "codex")
        self.assertEqual(entries[1].summary, "baseline (first checkpoint)")

    def test_restore_exact_and_undoable(self) -> None:
        repo = make_repo({"a.txt": "hi\n", "keep/b.txt": "b\n", "gone.md": "x\n"})
        s = Store(repo, tmpdir())
        base = s.snapshot_locked("base")["commit"]

        write_files(repo, {"a.txt": "edited\n", "new/c.txt": "c\n"})
        os.remove(os.path.join(repo, "gone.md"))
        after = s.snapshot_locked("agent")["commit"]

        s.restore(base)
        self.assertEqual(list_files(repo), ["a.txt", "gone.md", "keep/b.txt"])
        self.assertEqual((Path(repo) / "a.txt").read_text(encoding="utf-8"), "hi\n")
        self.assertFalse(os.path.exists(os.path.join(repo, "new")))

        s.restore(after)
        self.assertEqual(list_files(repo), ["a.txt", "keep/b.txt", "new/c.txt"])
        self.assertEqual((Path(repo) / "a.txt").read_text(encoding="utf-8"), "edited\n")

    def test_never_touches_real_git_index_or_history(self) -> None:
        repo = make_repo()
        write_files(repo, {"wip.txt": "uncommitted\n"})
        sh(["git", "add", "wip.txt"], cwd=repo)
        before = _git_status(repo)
        head = sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout

        s = Store(repo, tmpdir())
        base = s.snapshot_locked("base")["commit"]
        write_files(repo, {"wip.txt": "agent changed\n"})
        s.snapshot_locked("edit")
        s.restore(base)

        self.assertEqual(_git_status(repo), before)
        self.assertEqual(sh(["git", "rev-parse", "HEAD"], cwd=repo).stdout, head)
        self.assertEqual(sh(["git", "stash", "list"], cwd=repo).stdout, "")

    def test_keeps_force_added_ignored_files(self) -> None:
        repo = make_repo({".gitignore": "prs\n", "a.txt": "a\n"})
        write_files(repo, {"prs/README.md": "keep me\n"})
        sh(["git", "add", "-f", "prs/README.md"], cwd=repo)
        sh(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "force"], cwd=repo)

        s = Store(repo, tmpdir())
        base = s.snapshot_locked("base")["commit"]
        os.remove(os.path.join(repo, "prs/README.md"))
        s.snapshot_locked("deleted")
        s.restore(base)
        self.assertEqual((Path(repo) / "prs/README.md").read_text(encoding="utf-8"), "keep me\n")

    def test_ignored_build_files_untouched(self) -> None:
        repo = make_repo({".gitignore": "node_modules/\n", "a.txt": "a\n"})
        s = Store(repo, tmpdir())
        base = s.snapshot_locked("base")["commit"]
        write_files(repo, {"node_modules/pkg/index.js": "x\n", "a.txt": "b\n"})
        s.snapshot_locked("install")
        s.restore(base)
        self.assertTrue(os.path.exists(os.path.join(repo, "node_modules/pkg/index.js")))
        self.assertEqual((Path(repo) / "a.txt").read_text(encoding="utf-8"), "a\n")

    def test_respects_real_info_exclude(self) -> None:
        repo = make_repo()
        with open(os.path.join(repo, ".git", "info", "exclude"), "a", encoding="utf-8") as f:
            f.write("secret.env\n")
        write_files(repo, {"secret.env": "TOKEN=1\n"})
        s = Store(repo, tmpdir())
        base = s.snapshot_locked("base")["commit"]
        files = [x for x in s.g(["ls-tree", "-r", "--name-only", base]).stdout.splitlines() if x]
        self.assertEqual(files, ["a.txt"])

    def test_concurrent_snapshots_do_not_race(self) -> None:
        repo = make_repo()
        home = tmpdir()
        env = dict(os.environ, PYTHONPATH=SRC_DIR)
        procs = []
        for i in range(8):
            (Path(repo) / f"f{i}.txt").write_text(f"{i}\n", encoding="utf-8")
            code = f"from undokey.store import Store; Store({repo!r}, {home!r}).snapshot_locked('p{i}')"
            procs.append(subprocess.Popen([sys.executable, "-c", code], env=env, stderr=subprocess.PIPE, text=True))
        for p in procs:
            _, err = p.communicate(timeout=30)
            self.assertEqual(p.returncode, 0, err)
        s = Store(repo, home)
        files = sorted(x for x in s.g(["ls-tree", "--name-only", s.head()]).stdout.splitlines() if x)
        self.assertEqual(files, sorted(["a.txt", *(f"f{i}.txt" for i in range(8))]))

    def test_diff_parent_and_now(self) -> None:
        repo = make_repo()
        s = Store(repo, tmpdir())
        s.snapshot_locked("base")
        write_files(repo, {"a.txt": "hello world\n"})
        step = s.snapshot_locked("edit")["commit"]
        self.assertIn("+hello world", s.diff(step))
        write_files(repo, {"a.txt": "later\n"})
        self.assertIn("+later", s.diff(step, against="now"))


if __name__ == "__main__":
    unittest.main()
