# undokey

**A universal undo key for AI coding agents.**

Every tool call your agent makes becomes a restorable filesystem checkpoint — across **Codex CLI**, **OpenCode**, **Claude Code**, and **Antigravity** — without ever touching your project's `.git`.

For **Codex**, `undokey rewind <turn> --chat` rewinds **both your files and the conversation** (by forking the rollout into a new session; the original session stays untouched).

[中文文档](./README.zh-CN.md)

---

```text
$ undokey log
── 2026-11-21
● 9a098637  16:56:47  codex · Bash                                      1 file +0 -1
  36482239  16:56:47  outside change (before codex turn)                1 file +1 -0
  52e5ce27  16:56:47  codex · apply_patch                               2 files +2 -0
  d306f314  16:56:47  baseline (first checkpoint)                       1 file +1 -0

$ undokey turns
#1   codex       16:56:47  1 tools   refactor auth middleware
#2   codex       16:58:12  3 tools   rewrite database layer

$ undokey rewind 2 --chat
✓ files now match 52e5ce27 (before turn #2)
  changed your mind? undokey to 9a098637
✓ conversation forked to before turn #2 (original session untouched)
  continue with: codex resume 01a1fd49-cc5b-78d7-a474-d3860f5acdd3
```

---

## Why

- **Codex removed `/undo`** (and `/rewind` for code + chat is still one of the top open requests).
- **You switch between agents** — Codex, Claude Code, OpenCode, Antigravity — and each one has a different (or missing) checkpoint story.
- **`git checkout` isn't enough** when an agent edits uncommitted work, creates new files, deletes untracked files, or runs destructive shell commands mid-turn.

`undokey` sits at the filesystem layer behind your agents' native lifecycle hooks. It snapshots into an isolated shadow git repository under `~/.undokey/`, so:

- Your project's `.git`, staging index, commits, and stashes are **never touched**.
- Every restore takes a snapshot first — **even an undo can be undone**.
- Zero runtime dependencies (Node.js ≥ 18 + `git`).

## Install

```bash
npm install -g github:Simon-rss/undokey
```

Or from a local clone:

```bash
git clone https://github.com/Simon-rss/undokey.git
cd undokey && npm link
```

## Quick Start

```bash
# 1. Auto-detect installed agents and register hooks (backs up existing configs)
undokey init

# 2. Use your agent as usual in any git repository
#    (For Codex, run /hooks inside codex once to approve the undokey entries.)

# 3. Inspect or rewind anytime
undokey log                 # checkpoint timeline
undokey diff <id>           # what that step changed (--now compares to current files)
undokey undo                # step back 1 checkpoint (or: undokey undo 3)
undokey to <id>             # jump files to any checkpoint
undokey turns               # group checkpoints by agent turn
undokey rewind 2 --chat     # restore files to before turn #2 + fork Codex chat
```

Want hooks scoped only to the current repo instead of user-wide?

```bash
undokey init --project
```

This writes project-local hook configs and automatically adds them to `.git/info/exclude` so they stay out of your commits.

## Agent Support

| Agent | Integration | File checkpoints | Conversation rewind (`--chat`) | Status |
| --- | --- | :---: | :---: | --- |
| **Codex CLI** | `~/.codex/hooks.json` (`PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Stop`) | ✅ | ✅ (forks rollout) | Live tested |
| **OpenCode** | `~/.config/opencode/plugin/undokey.js` | ✅ | — | Live tested |
| **Claude Code** | `~/.claude/settings.json` hooks | ✅ | Use built-in `/rewind` | Hook format tested |
| **Antigravity** | `~/.gemini/config/hooks.json` (`PreInvocation`, `PostToolUse`, `Stop`) | ✅ | — | Hook contract tested |

> **Note on Codex `--chat`**: Codex rollout files are an internal format. `undokey` never modifies the original file — it writes a truncated copy with a fresh UUIDv7 session id so you can safely `codex resume <new-id>`. If Codex changes its rollout schema in a future release, file checkpoints and `undokey rewind <turn>` (without `--chat`) still work unchanged.

## Commands

| Command | Description |
| --- | --- |
| `undokey init [--agent list] [--project]` | Install hooks for detected (or specified) agents |
| `undokey uninstall [--agent list] [--project]` | Remove undokey hooks (keeps recorded checkpoints) |
| `undokey status` | Show installed hooks and checkpoints recorded for this project |
| `undokey log [-n 30]` | Show checkpoint timeline for the current project |
| `undokey diff <id> [--now] [--stat]` | Show diff of a checkpoint vs. its parent (or vs. current files with `--now`) |
| `undokey undo [n]` | Restore files to `n` checkpoints earlier (always redoable) |
| `undokey to <id>` | Restore files to a specific checkpoint (always redoable) |
| `undokey turns` | List agent turns (prompt, tool count, status) |
| `undokey rewind <turn> [--after] [--chat]` | Restore files to before (or `--after`) a turn; `--chat` forks Codex conversation |
| `undokey snapshot [dir] [--label text]` | Create a manual checkpoint |
| `undokey track [dir]` / `untrack [dir]` | Opt a non-git directory into recording |

## How It Handles Real-World Edge Cases

- **Parallel tool calls**: Agents fire hooks concurrently when running multiple tools in parallel. All shadow-repo operations are serialized with a lock (with stale-lock recovery) so checkpoints are never dropped or corrupted.
- **Tracked-but-ignored files**: Files matching `.gitignore` that were force-added (`git add -f`) into the real repo are explicitly preserved in snapshots so a restore never deletes them.
- **Ignored build artifacts**: `node_modules/`, build outputs, and `.git/info/exclude` entries are neither snapshotted nor wiped during a restore.
- **Background agent tasks**: Internal agent directories (such as `~/.codex/memories`, `~/.claude`, `~/.gemini`) are automatically ignored.
- **Large repositories**: If a repo has > 3,000 tracked files and no baseline yet, the first snapshot runs detached in the background so your agent isn't blocked. Subsequent incremental snapshots typically take ~60–190 ms (measured on a 205 MB / 10k-file repo on Apple Silicon).
- **Fail-safe hooks**: `undokey hook` always exits `0` and logs errors to `~/.undokey/errors.log` so a hook failure never interrupts your agent.

## Development

```bash
npm run lint
npm test
```

## License

[MIT](./LICENSE)
