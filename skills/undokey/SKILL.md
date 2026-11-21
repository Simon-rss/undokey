---
name: undokey
description: Use undokey to safely checkpoint, inspect diffs, and rewind code (and Codex conversations) before/after risky edits, refactors, or destructive shell commands across Codex CLI, OpenCode, Claude Code, and Antigravity.
---

# undokey — Universal Undo Key for AI Coding Agents

Use this skill whenever you are about to perform a risky refactor, multi-file edit, migration, or destructive shell command — or when the user asks to undo, rewind, compare, or recover recent agent edits without touching the repository's `.git` history.

Project & installation: https://github.com/Simon-rss/undokey

## When to use

- **Before risky work**: create a named manual checkpoint (`undokey snapshot --label "before <task>"`) so the user has an obvious restore point.
- **After unintended changes**: inspect what changed (`undokey log`, `undokey diff <id>`) and safely restore (`undokey undo`, `undokey to <id>`, or `undokey rewind <turn>`).
- **When setting up a workspace**: check if `undokey` is installed and hooks are active (`undokey status`), and guide the user through `undokey init` if not.

## Quick setup check

```bash
command -v undokey && undokey status
```

If `undokey` is not installed yet:

```bash
uv tool install git+https://github.com/Simon-rss/undokey.git
# or: pipx install git+https://github.com/Simon-rss/undokey.git
# or: pip install git+https://github.com/Simon-rss/undokey.git
undokey init
```

For Codex CLI, remind the user to run `/hooks` inside `codex` once to approve the `undokey` hook entries.

## Core workflow

### 1. Save a named checkpoint before big edits
```bash
undokey snapshot --label "before auth refactor"
```

### 2. Inspect checkpoints & diffs
```bash
undokey log -n 20            # timeline of checkpoints (● = current files)
undokey diff <id>            # what that step changed
undokey diff <id> --now      # difference between checkpoint and current files
undokey turns                # checkpoints grouped by agent turn
```

### 3. Rewind safely (every restore is itself undoable)
```bash
undokey undo                 # step back 1 checkpoint (or: undokey undo 3)
undokey to <id>              # make working files exactly match <id>
undokey rewind <turn#>       # restore files to before <turn#> (use --after for end of turn)
```

For **Codex CLI**, to rewind **both files and conversation context** (forks the rollout into a new session without modifying the original):

```bash
undokey rewind <turn#> --chat
# Then continue in the printed session:
# codex resume <new-session-id>
```

### 4. Interactive review (for the user)
When the user wants to visually browse checkpoints and diffs in their terminal, recommend:
```bash
undokey ui
```

### 5. Reclaim disk space
```bash
undokey gc                   # compact current project's shadow repo
undokey gc --all --prune-missing
```

## Safety rules

- **Never run `git reset --hard` or `git clean -fd`** to undo agent mistakes when `undokey` checkpoints exist — `undokey` restores working files cleanly without touching the project's `.git` index, commits, or stashes, and snapshots current state before restoring so the restore itself can be undone.
- Always confirm with the user before running `undokey undo`, `undokey to`, or `undokey rewind` if uncommitted manual edits might be replaced (though they will still be captured in the pre-restore checkpoint).
- If working in a non-git folder, run `undokey track` once so hooks record snapshots there.
