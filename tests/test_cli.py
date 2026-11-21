from __future__ import annotations

import json
import os
import re
import unittest
from pathlib import Path

from helpers import cli, hook, list_files, make_repo, tmpdir, write_files


def _make_codex_rollout(directory: str, session_id: str) -> str:
    file = os.path.join(directory, f"rollout-2026-11-21T10-00-00-{session_id}.jsonl")
    lines = [
        {"type": "session_meta", "payload": {"id": session_id, "session_id": session_id}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-1"}},
        {"type": "response_item", "payload": {"type": "message", "text": "create hello", "session_id": session_id}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-1"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "turn-2"}},
        {"type": "response_item", "payload": {"type": "message", "text": "create world", "session_id": session_id}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "turn-2"}},
    ]
    Path(file).write_text("\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return file


class CliTests(unittest.TestCase):
    def test_init_and_uninstall_preserve_existing_hooks(self) -> None:
        home = tmpdir()
        repo = make_repo()

        codex_file = Path(home) / ".codex" / "hooks.json"
        codex_file.parent.mkdir(parents=True, exist_ok=True)
        codex_file.write_text(
            json.dumps({"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}]}}),
            encoding="utf-8",
        )
        claude_file = Path(home) / ".claude" / "settings.json"
        claude_file.parent.mkdir(parents=True, exist_ok=True)
        claude_file.write_text(json.dumps({"theme": "dark"}), encoding="utf-8")

        r1 = cli(["init", "--agent", "codex,claude,opencode,antigravity"], home=home, cwd=repo)
        self.assertEqual(r1["code"], 0, r1["err"])
        for a in ("codex", "claude", "opencode", "antigravity"):
            self.assertRegex(r1["out"], rf"{a}\s+installed")

        codex_cfg = json.loads(codex_file.read_text(encoding="utf-8"))
        self.assertEqual(codex_cfg["hooks"]["SessionStart"][0]["hooks"][0]["command"], "echo hi")
        self.assertEqual(len(codex_cfg["hooks"]["PostToolUse"]), 1)

        claude_cfg = json.loads(claude_file.read_text(encoding="utf-8"))
        self.assertEqual(claude_cfg["theme"], "dark")
        self.assertEqual(len(claude_cfg["hooks"]["PostToolUse"]), 1)

        r2 = cli(["init", "--agent", "codex,claude,opencode,antigravity"], home=home, cwd=repo)
        self.assertEqual(r2["code"], 0)
        self.assertRegex(r2["out"], r"codex\s+already installed")

        st = cli(["status"], home=home, cwd=repo)
        self.assertEqual(st["code"], 0)
        self.assertIn("user-wide", st["out"])

        r3 = cli(["uninstall"], home=home, cwd=repo)
        self.assertEqual(r3["code"], 0)
        codex_after = json.loads(codex_file.read_text(encoding="utf-8"))
        self.assertEqual(codex_after["hooks"]["SessionStart"][0]["hooks"][0]["command"], "echo hi")
        self.assertNotIn("PostToolUse", codex_after["hooks"])
        self.assertFalse((Path(home) / ".config" / "opencode" / "plugin" / "undokey.js").exists())

    def test_project_init_excludes_local_hook_files(self) -> None:
        home = tmpdir()
        repo = make_repo()
        r = cli(["init", "--project", "--agent", "codex,opencode"], home=home, cwd=repo)
        self.assertEqual(r["code"], 0, r["err"])
        self.assertTrue((Path(repo) / ".codex" / "hooks.json").exists())
        self.assertTrue((Path(repo) / ".opencode" / "plugin" / "undokey.js").exists())
        exclude = (Path(repo) / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".codex/hooks.json", exclude)
        self.assertIn(".opencode/plugin/undokey.js", exclude)

    def test_ignores_internal_dirs_and_malformed_json(self) -> None:
        home = tmpdir()
        mem = Path(home) / ".codex" / "memories"
        mem.mkdir(parents=True, exist_ok=True)
        (mem / "m.txt").write_text("private\n", encoding="utf-8")

        r1 = hook("codex", {"hook_event_name": "PostToolUse", "tool_name": "Bash", "cwd": str(mem)}, home=home)
        self.assertEqual(r1["code"], 0)
        self.assertFalse((Path(home) / ".undokey" / "repos").exists())

        r2 = hook("codex", "{not json", home=home)
        self.assertEqual(r2["code"], 0)

        r3 = hook("antigravity", {"hookEventName": "PostToolUse", "cwd": str(mem)}, home=home)
        self.assertEqual(r3["code"], 0)
        self.assertEqual(r3["out"].strip(), "{}")

    def test_track_plain_directory(self) -> None:
        home = tmpdir()
        d = os.path.join(tmpdir(), "notes")
        os.makedirs(d)
        (Path(d) / "n.txt").write_text("v1\n", encoding="utf-8")

        hook("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "s", "turn_id": "t", "cwd": d}, home=home)
        self.assertEqual(cli(["log"], home=home, cwd=d)["code"], 1)

        self.assertEqual(cli(["track"], home=home, cwd=d)["code"], 0)
        hook("codex", {"hook_event_name": "UserPromptSubmit", "session_id": "s", "turn_id": "t", "cwd": d}, home=home)
        (Path(d) / "n.txt").write_text("v2\n", encoding="utf-8")
        hook("codex", {"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "session_id": "s", "turn_id": "t", "cwd": d}, home=home)

        log = cli(["log"], home=home, cwd=d)
        self.assertEqual(log["code"], 0, log["err"])
        self.assertIn("codex · apply_patch", log["out"])

        self.assertEqual(cli(["undo"], home=home, cwd=d)["code"], 0)
        self.assertEqual((Path(d) / "n.txt").read_text(encoding="utf-8"), "v1\n")

    def test_turns_and_codex_rewind_chat(self) -> None:
        home = tmpdir()
        repo = make_repo({"README.md": "# demo\n"})
        sessions_dir = os.path.join(home, ".codex", "sessions")
        os.makedirs(sessions_dir, exist_ok=True)
        orig_id = "019a0000-0000-7000-8000-000000000001"
        rollout = _make_codex_rollout(sessions_dir, orig_id)
        orig_content = Path(rollout).read_text(encoding="utf-8")

        hook("codex", {"hook_event_name": "UserPromptSubmit", "session_id": orig_id, "turn_id": "turn-1", "cwd": repo, "prompt": "create hello", "transcript_path": rollout}, home=home)
        write_files(repo, {"hello.txt": "hi\n"})
        hook("codex", {"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "session_id": orig_id, "turn_id": "turn-1", "cwd": repo, "transcript_path": rollout}, home=home)
        hook("codex", {"hook_event_name": "Stop", "session_id": orig_id, "turn_id": "turn-1", "cwd": repo, "transcript_path": rollout}, home=home)

        hook("codex", {"hook_event_name": "UserPromptSubmit", "session_id": orig_id, "turn_id": "turn-2", "cwd": repo, "prompt": "create world", "transcript_path": rollout}, home=home)
        write_files(repo, {"world.txt": "earth\n"})
        hook("codex", {"hook_event_name": "PostToolUse", "tool_name": "apply_patch", "session_id": orig_id, "turn_id": "turn-2", "cwd": repo, "transcript_path": rollout}, home=home)
        hook("codex", {"hook_event_name": "Stop", "session_id": orig_id, "turn_id": "turn-2", "cwd": repo, "transcript_path": rollout}, home=home)

        turns = cli(["turns"], home=home, cwd=repo)
        self.assertEqual(turns["code"], 0, turns["err"])
        self.assertRegex(turns["out"], r"#1\s+codex")
        self.assertRegex(turns["out"], r"#2\s+codex")

        rw = cli(["rewind", "2", "--chat"], home=home, cwd=repo)
        self.assertEqual(rw["code"], 0, rw["err"])
        self.assertEqual(list_files(repo), ["README.md", "hello.txt"])

        m = re.search(r"codex resume ([0-9a-f-]{36})", rw["out"])
        self.assertIsNotNone(m, rw["out"])
        new_id = m.group(1)
        self.assertNotEqual(new_id, orig_id)
        self.assertEqual(Path(rollout).read_text(encoding="utf-8"), orig_content)

        forked_files = [f for f in os.listdir(sessions_dir) if new_id in f]
        self.assertEqual(len(forked_files), 1)
        forked_lines = [json.loads(ln) for ln in (Path(sessions_dir) / forked_files[0]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(forked_lines[0]["payload"]["id"], new_id)
        self.assertTrue(any(ln.get("payload", {}).get("turn_id") == "turn-1" for ln in forked_lines))
        self.assertFalse(any(ln.get("payload", {}).get("turn_id") == "turn-2" for ln in forked_lines))

        rw1 = cli(["rewind", "1", "--chat"], home=home, cwd=repo)
        self.assertEqual(rw1["code"], 1)
        self.assertIn("first turn", rw1["err"])

    def test_antigravity_camelcase_hook(self) -> None:
        home = tmpdir()
        repo = make_repo()
        r1 = hook("antigravity", {"hookEventName": "PreInvocation", "conversationId": "c1", "cwd": repo, "lastUserInput": "edit a"}, home=home)
        self.assertEqual(r1["code"], 0)
        self.assertEqual(r1["out"].strip(), "{}")

        write_files(repo, {"a.txt": "from antigravity\n"})
        r2 = hook("antigravity", {"hookEventName": "PostToolUse", "conversationId": "c1", "cwd": repo, "toolCall": {"id": "tc1", "name": "edit_file"}}, home=home)
        self.assertEqual(r2["code"], 0)
        self.assertEqual(r2["out"].strip(), "{}")

        log = cli(["log"], home=home, cwd=repo)
        self.assertIn("antigravity · edit_file", log["out"])
        self.assertEqual(cli(["undo"], home=home, cwd=repo)["code"], 0)
        self.assertEqual((Path(repo) / "a.txt").read_text(encoding="utf-8"), "hi\n")


if __name__ == "__main__":
    unittest.main()
