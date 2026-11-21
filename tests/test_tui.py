from __future__ import annotations

import os
import unittest
from pathlib import Path

from helpers import cli, make_repo, tmpdir, write_files
from undokey.store import Store
from undokey.tui import TuiState


class TuiTests(unittest.TestCase):
    def test_tui_state_navigation_diff_and_restore(self) -> None:
        repo = make_repo({"app.py": "print('v1')\n"})
        store = Store(repo, tmpdir())
        store.snapshot_locked("base")

        write_files(repo, {"app.py": "print('v2')\n"})
        store.snapshot_locked("codex · apply_patch", {"agent": "codex", "tool": "apply_patch"})

        write_files(repo, {"app.py": "print('v3')\n", "extra.py": "x = 1\n"})
        store.snapshot_locked("codex · Bash", {"agent": "codex", "tool": "Bash"})

        state = TuiState(store)
        self.assertEqual(len(state.entries), 3)
        self.assertEqual(state.selected, 0)
        self.assertEqual(state.selected_entry.summary, "codex · Bash")

        # Diff vs parent shows v3 & extra.py
        d0 = "\n".join(state.current_diff_lines())
        self.assertIn("print('v3')", d0)

        # Move down to v2 checkpoint
        state.move(1)
        self.assertEqual(state.selected, 1)
        self.assertEqual(state.selected_entry.summary, "codex · apply_patch")
        self.assertIn("print('v2')", "\n".join(state.current_diff_lines()))

        # Toggle diff mode to vs now
        state.toggle_diff_mode()
        self.assertEqual(state.diff_mode, "now")
        self.assertIn("+print('v3')", "\n".join(state.current_diff_lines()))

        # Scroll diff safely clamps
        state.scroll_diff(999, page_height=5)
        state.scroll_diff(-999, page_height=5)
        self.assertEqual(state.diff_offset, 0)

        # Restore selected (v2)
        res = state.restore_selected()
        self.assertIsNotNone(res)
        self.assertEqual((Path(repo) / "app.py").read_text(encoding="utf-8"), "print('v2')\n")
        self.assertFalse(os.path.exists(os.path.join(repo, "extra.py")))
        self.assertIn("Restored files to", state.message)

        # Undo one step inside TUI brings us back to v1 (parent of v2)
        state.undo_one()
        self.assertEqual((Path(repo) / "app.py").read_text(encoding="utf-8"), "print('v1')\n")

    def test_ui_command_errors_gracefully_without_tty(self) -> None:
        home = tmpdir()
        repo = make_repo()
        cli(["snapshot"], home=home, cwd=repo)
        r = cli(["ui"], home=home, cwd=repo)
        self.assertEqual(r["code"], 1)
        self.assertIn("interactive terminal", r["err"])

    def test_gc_and_prune_missing(self) -> None:
        import shutil

        home = tmpdir()
        repo = make_repo()
        cli(["snapshot"], home=home, cwd=repo)
        write_files(repo, {"a.txt": "updated\n"})
        cli(["snapshot"], home=home, cwd=repo)

        r = cli(["gc"], home=home, cwd=repo)
        self.assertEqual(r["code"], 0, r["err"])
        self.assertIn("compacted shadow store", r["out"])

        # Remove repo directory and prune missing store
        shutil.rmtree(repo)
        r2 = cli(["gc", "--all", "--prune-missing"], home=home, cwd=home)
        self.assertEqual(r2["code"], 0, r2["err"])
        self.assertIn("pruned missing worktree", r2["out"])


if __name__ == "__main__":
    unittest.main()
