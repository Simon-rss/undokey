import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { makeRepo, tmpdir, writeFiles, cli, hook, listFiles } from "./helpers.js";

function makeCodexRollout(dir, id) {
  const file = path.join(dir, `rollout-2026-11-21T10-00-00-${id}.jsonl`);
  const lines = [
    { type: "session_meta", payload: { id, session_id: id } },
    { type: "event_msg", payload: { type: "task_started", turn_id: "turn-1" } },
    { type: "response_item", payload: { type: "message", text: "create hello", session_id: id } },
    { type: "event_msg", payload: { type: "task_complete", turn_id: "turn-1" } },
    { type: "event_msg", payload: { type: "task_started", turn_id: "turn-2" } },
    { type: "response_item", payload: { type: "message", text: "create world", session_id: id } },
    { type: "event_msg", payload: { type: "task_complete", turn_id: "turn-2" } },
  ];
  fs.writeFileSync(file, lines.map((x) => JSON.stringify(x)).join("\n") + "\n");
  return file;
}

test("init & uninstall preserve existing hooks, back up files, and clean up", () => {
  const home = tmpdir();
  const repo = makeRepo();

  // Seed existing hooks in codex & claude to verify we merge instead of clobbering
  const codexFile = path.join(home, ".codex", "hooks.json");
  fs.mkdirSync(path.dirname(codexFile), { recursive: true });
  fs.writeFileSync(
    codexFile,
    JSON.stringify({ hooks: { SessionStart: [{ matcher: "startup", hooks: [{ type: "command", command: "echo hi" }] }] } }),
  );
  const claudeFile = path.join(home, ".claude", "settings.json");
  fs.mkdirSync(path.dirname(claudeFile), { recursive: true });
  fs.writeFileSync(claudeFile, JSON.stringify({ theme: "dark" }));

  const r1 = cli(["init", "--agent", "codex,claude,opencode,antigravity"], { home, cwd: repo });
  assert.equal(r1.code, 0, r1.err);
  assert.match(r1.out, /codex\s+installed/);
  assert.match(r1.out, /claude\s+installed/);
  assert.match(r1.out, /opencode\s+installed/);
  assert.match(r1.out, /antigravity\s+installed/);

  // Existing entries preserved
  const codexCfg = JSON.parse(fs.readFileSync(codexFile, "utf8"));
  assert.equal(codexCfg.hooks.SessionStart[0].hooks[0].command, "echo hi");
  assert.equal(codexCfg.hooks.PostToolUse.length, 1);

  const claudeCfg = JSON.parse(fs.readFileSync(claudeFile, "utf8"));
  assert.equal(claudeCfg.theme, "dark");
  assert.equal(claudeCfg.hooks.PostToolUse.length, 1);

  // Idempotent second run
  const r2 = cli(["init", "--agent", "codex,claude,opencode,antigravity"], { home, cwd: repo });
  assert.equal(r2.code, 0);
  assert.match(r2.out, /codex\s+already installed/);

  // Status reports installed
  const st = cli(["status"], { home, cwd: repo });
  assert.equal(st.code, 0);
  assert.match(st.out, /user-wide/);

  // Uninstall removes our hooks and keeps existing ones
  const r3 = cli(["uninstall"], { home, cwd: repo });
  assert.equal(r3.code, 0);
  const codexAfter = JSON.parse(fs.readFileSync(codexFile, "utf8"));
  assert.equal(codexAfter.hooks.SessionStart[0].hooks[0].command, "echo hi");
  assert.equal(codexAfter.hooks.PostToolUse, undefined);
  assert.equal(fs.existsSync(path.join(home, ".config", "opencode", "plugin", "undokey.js")), false);
});

test("project-scoped init writes local files and excludes them via .git/info/exclude", () => {
  const home = tmpdir();
  const repo = makeRepo();
  const r = cli(["init", "--project", "--agent", "codex,opencode"], { home, cwd: repo });
  assert.equal(r.code, 0, r.err);
  assert.equal(fs.existsSync(path.join(repo, ".codex", "hooks.json")), true);
  assert.equal(fs.existsSync(path.join(repo, ".opencode", "plugin", "undokey.js")), true);
  const exclude = fs.readFileSync(path.join(repo, ".git", "info", "exclude"), "utf8");
  assert.match(exclude, /\.codex\/hooks\.json/);
  assert.match(exclude, /\.opencode\/plugin\/undokey\.js/);
});

test("hooks ignore internal agent directories (e.g. ~/.codex/memories) and malformed payloads never fail", () => {
  const home = tmpdir();
  const mem = path.join(home, ".codex", "memories");
  fs.mkdirSync(mem, { recursive: true });
  fs.writeFileSync(path.join(mem, "m.txt"), "private\n");

  const r1 = hook("codex", { hook_event_name: "PostToolUse", tool_name: "Bash", cwd: mem }, { home });
  assert.equal(r1.code, 0);
  assert.equal(fs.existsSync(path.join(home, ".undokey", "repos")), false);

  const r2 = hook("codex", "{not json", { home });
  assert.equal(r2.code, 0);

  // Antigravity expects "{}\n" on stdout even when ignored or normal
  const r3 = hook("antigravity", { hookEventName: "PostToolUse", cwd: mem }, { home });
  assert.equal(r3.code, 0);
  assert.equal(r3.out.trim(), "{}");
});

test("plain directory is ignored until `undokey track` is enabled", () => {
  const home = tmpdir();
  const dir = path.join(tmpdir(), "notes");
  fs.mkdirSync(dir);
  fs.writeFileSync(path.join(dir, "n.txt"), "v1\n");

  hook("codex", { hook_event_name: "UserPromptSubmit", session_id: "s", turn_id: "t", cwd: dir }, { home });
  assert.equal(cli(["log"], { home, cwd: dir }).code, 1);

  assert.equal(cli(["track"], { home, cwd: dir }).code, 0);
  hook("codex", { hook_event_name: "UserPromptSubmit", session_id: "s", turn_id: "t", cwd: dir }, { home });
  fs.writeFileSync(path.join(dir, "n.txt"), "v2\n");
  hook("codex", { hook_event_name: "PostToolUse", tool_name: "apply_patch", session_id: "s", turn_id: "t", cwd: dir }, { home });

  const log = cli(["log"], { home, cwd: dir });
  assert.equal(log.code, 0, log.err);
  assert.match(log.out, /codex · apply_patch/);

  assert.equal(cli(["undo"], { home, cwd: dir }).code, 0);
  assert.equal(fs.readFileSync(path.join(dir, "n.txt"), "utf8"), "v1\n");
});

test("full turns + rewind --chat (Codex) restores files and forks rollout without touching original", () => {
  const home = tmpdir();
  const repo = makeRepo({ "README.md": "# demo\n" });
  const sessionsDir = path.join(home, ".codex", "sessions");
  fs.mkdirSync(sessionsDir, { recursive: true });
  const origId = "019a0000-0000-7000-8000-000000000001";
  const rollout = makeCodexRollout(sessionsDir, origId);
  const origContent = fs.readFileSync(rollout, "utf8");

  // Turn 1: create hello.txt
  hook("codex", { hook_event_name: "UserPromptSubmit", session_id: origId, turn_id: "turn-1", cwd: repo, prompt: "create hello", transcript_path: rollout }, { home });
  writeFiles(repo, { "hello.txt": "hi\n" });
  hook("codex", { hook_event_name: "PostToolUse", tool_name: "apply_patch", session_id: origId, turn_id: "turn-1", cwd: repo, transcript_path: rollout }, { home });
  hook("codex", { hook_event_name: "Stop", session_id: origId, turn_id: "turn-1", cwd: repo, transcript_path: rollout }, { home });

  // Turn 2: create world.txt
  hook("codex", { hook_event_name: "UserPromptSubmit", session_id: origId, turn_id: "turn-2", cwd: repo, prompt: "create world", transcript_path: rollout }, { home });
  writeFiles(repo, { "world.txt": "earth\n" });
  hook("codex", { hook_event_name: "PostToolUse", tool_name: "apply_patch", session_id: origId, turn_id: "turn-2", cwd: repo, transcript_path: rollout }, { home });
  hook("codex", { hook_event_name: "Stop", session_id: origId, turn_id: "turn-2", cwd: repo, transcript_path: rollout }, { home });

  const turns = cli(["turns"], { home, cwd: repo });
  assert.equal(turns.code, 0, turns.err);
  assert.match(turns.out, /#1\s+codex/);
  assert.match(turns.out, /#2\s+codex/);

  // Rewind to before turn 2 (= end of turn 1) with --chat
  const rw = cli(["rewind", "2", "--chat"], { home, cwd: repo });
  assert.equal(rw.code, 0, rw.err);
  assert.deepEqual(listFiles(repo), ["README.md", "hello.txt"]);

  const m = rw.out.match(/codex resume ([0-9a-f-]{36})/);
  assert.ok(m, `expected resume id in output:\n${rw.out}`);
  const newId = m[1];
  assert.notEqual(newId, origId);

  // Original rollout untouched
  assert.equal(fs.readFileSync(rollout, "utf8"), origContent);

  // New rollout keeps only turn 1 and has rewritten session id
  const created = fs.readdirSync(sessionsDir).find((f) => f.includes(newId));
  assert.ok(created, "forked rollout file should exist");
  const forkedLines = fs.readFileSync(path.join(sessionsDir, created), "utf8").trim().split("\n").map((l) => JSON.parse(l));
  assert.equal(forkedLines[0].payload.id, newId);
  assert.equal(forkedLines.some((l) => l.payload?.turn_id === "turn-1"), true);
  assert.equal(forkedLines.some((l) => l.payload?.turn_id === "turn-2"), false);

  // Rewinding before turn 1 with --chat should explain there is nothing to keep
  const rw1 = cli(["rewind", "1", "--chat"], { home, cwd: repo });
  assert.equal(rw1.code, 1);
  assert.match(rw1.err, /first turn/);
});

test("antigravity camelCase hook records checkpoints and outputs {}", () => {
  const home = tmpdir();
  const repo = makeRepo();
  const r1 = hook("antigravity", { hookEventName: "PreInvocation", conversationId: "c1", cwd: repo, lastUserInput: "edit a" }, { home });
  assert.equal(r1.code, 0);
  assert.equal(r1.out.trim(), "{}");

  writeFiles(repo, { "a.txt": "from antigravity\n" });
  const r2 = hook("antigravity", { hookEventName: "PostToolUse", conversationId: "c1", cwd: repo, toolCall: { id: "tc1", name: "edit_file" } }, { home });
  assert.equal(r2.code, 0);
  assert.equal(r2.out.trim(), "{}");

  const log = cli(["log"], { home, cwd: repo });
  assert.match(log.out, /antigravity · edit_file/);
  assert.equal(cli(["undo"], { home, cwd: repo }).code, 0);
  assert.equal(fs.readFileSync(path.join(repo, "a.txt"), "utf8"), "hi\n");
});
