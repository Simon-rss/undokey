from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from undokey import __version__
from undokey.git import git, real_repo_root
from undokey.hook import handle_hook
from undokey.install import AGENTS, detect_agents, install, is_installed, uninstall
from undokey.store import TIMELINE_REF, Store, list_stores, undokey_home
from undokey.tui import run_tui
from undokey.turns import fork_codex_session, turns_from_events
from undokey.workspace import read_config, resolve_worktree, write_config

_PROJECT_FILES = (
    ".codex/hooks.json",
    ".claude/settings.local.json",
    ".agents/hooks.json",
    ".opencode/plugin/undokey.js",
)


def _use_color() -> bool:
    return sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _c(code: int, s: Any) -> str:
    text = str(s)
    return f"\x1b[{code}m{text}\x1b[0m" if _use_color() else text


def _dim(s: Any) -> str:
    return _c(2, s)


def _bold(s: Any) -> str:
    return _c(1, s)


def _green(s: Any) -> str:
    return _c(32, s)


def _yellow(s: Any) -> str:
    return _c(33, s)


def _red(s: Any) -> str:
    return _c(31, s)


def _cyan(s: Any) -> str:
    return _c(36, s)


def _help_text() -> str:
    return f"""{_bold("undokey")} {__version__} — a universal undo key for AI coding agents

{_bold("Setup")}
  undokey init [--agent codex,claude,opencode,antigravity] [--project]
                              install hooks (default: every detected agent, user-wide)
  undokey uninstall [--agent ...] [--project]
  undokey status              what is installed, and what is recorded for this project

{_bold("Everyday")}
  undokey ui [-n 200]         interactive timeline + live diff preview (Enter to restore)
  undokey log [-n 30]         timeline of checkpoints for the current project
  undokey diff <id> [--now] [--stat]
                              what a step changed (or: checkpoint vs. current files)
  undokey undo [n]            step back n checkpoints (default 1); always redoable
  undokey to <id>             make the files exactly match a checkpoint; always redoable

{_bold("Turns")}
  undokey turns               agent turns (prompt -> tool calls -> end) for this project
  undokey rewind <turn> [--after] [--chat]
                              restore files to before <turn> (or after it with --after);
                              --chat (Codex): also fork the conversation to that point

{_bold("Other")}
  undokey snapshot [dir] [--label text]   manual checkpoint
  undokey gc [--all] [--prune-missing]    compact shadow repos and reclaim disk space
  undokey track [dir] / untrack [dir]     record a directory that is not a git repo
  undokey hook <agent>                    (internal) called by agent hooks

Data lives in {_dim(undokey_home())}; your project's own .git is never touched."""


def _parse_args(argv: Sequence[str]) -> Dict[str, Any]:
    args: Dict[str, Any] = {"_": []}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if "=" in a:
                k, v = a[2:].split("=", 1)
                args[k] = v
            else:
                k = a[2:]
                if i + 1 < len(argv) and not argv[i + 1].startswith("-") and k in ("agent", "label", "n"):
                    i += 1
                    args[k] = argv[i]
                else:
                    args[k] = True
        elif a == "-n" and i + 1 < len(argv):
            i += 1
            args["n"] = argv[i]
        elif a == "-h":
            args["help"] = True
        else:
            args["_"].append(a)
        i += 1
    return args


def _die(msg: str) -> None:
    print(_red(f"undokey: {msg}"), file=sys.stderr)
    raise SystemExit(1)


def _store_here(directory: Optional[str] = None) -> Store:
    d = directory or os.getcwd()
    wt = resolve_worktree(d)
    if not wt:
        _die(f"{d} is not inside a git repository (use `undokey track` to record a plain directory).")
    return Store(wt)


def _require_history(store: Store) -> None:
    if not store.head():
        _die(f"nothing recorded yet for {store.worktree}. Run `undokey init`, then use your agent as usual.")


def _pad(s: str, width: int) -> str:
    w = sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)
    return s if w >= width else s + " " * (width - w)


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _selected_agents(args: Dict[str, Any]) -> List[str]:
    if args.get("agent") and args["agent"] is not True:
        chosen = [s.strip() for s in str(args["agent"]).split(",") if s.strip()]
        for a in chosen:
            if a not in AGENTS:
                _die(f'unknown agent "{a}" (choose from {", ".join(AGENTS)})')
        return chosen
    found = detect_agents()
    chosen = [a for a in AGENTS if found[a]]
    if not chosen:
        _die(f"no supported agent found. Use --agent to choose from {', '.join(AGENTS)}.")
    return chosen


def _project_root_or_die() -> str:
    root = real_repo_root(os.getcwd())
    if not root:
        _die("--project needs to run inside a git repository.")
    return root


def _exclude_project_files(root: str) -> None:
    r = git(["rev-parse", "--git-path", "info/exclude"], cwd=root, allow_fail=True)
    if r.code != 0:
        return
    file = Path(root) / r.stdout.strip()
    cur = file.read_text(encoding="utf-8") if file.exists() else ""
    existing = set(cur.splitlines())
    missing = [p for p in _PROJECT_FILES if p not in existing]
    if not missing:
        return
    file.parent.mkdir(parents=True, exist_ok=True)
    prefix = "\n" if cur and not cur.endswith("\n") else ""
    with file.open("a", encoding="utf-8") as f:
        f.write(f"{prefix}# undokey (local hook config)\n" + "\n".join(missing) + "\n")


def _cmd_init(args: Dict[str, Any]) -> None:
    scope = "project" if args.get("project") else "global"
    root = _project_root_or_die() if args.get("project") else None
    for agent in _selected_agents(args):
        try:
            r = install(agent, scope, root)
            status = "installed" if r["changed"] else "already installed"
            print(f"{_green('✓')} {_pad(agent, 12)} {status}  {_dim(r['file'])}")
            if r.get("backup"):
                print(f"  {_dim('backup: ' + r['backup'])}")
            if r.get("note"):
                print(f"  {_yellow('!')} {r['note']}")
        except Exception as e:
            print(f"{_red('✗')} {_pad(agent, 12)} {e}")
    if root:
        _exclude_project_files(root)
    print(f"\nDone. Use your agent as usual, then try {_cyan('undokey log')} inside the project.")


def _cmd_uninstall(args: Dict[str, Any]) -> None:
    scope = "project" if args.get("project") else "global"
    root = _project_root_or_die() if args.get("project") else None
    chosen = _selected_agents(args) if args.get("agent") else list(AGENTS)
    for agent in chosen:
        try:
            r = uninstall(agent, scope, root)
            if r["changed"]:
                print(f"{_green('✓')} {_pad(agent, 12)} removed  {_dim(r['file'])}")
        except Exception as e:
            print(f"{_red('✗')} {_pad(agent, 12)} {e}")
    print(_dim(f"Recorded checkpoints are kept in {undokey_home()}."))


def _cmd_status() -> None:
    found = detect_agents()
    root = real_repo_root(os.getcwd())
    print(_bold("Agents"))
    for a in AGENTS:
        g = is_installed(a, "global")
        p = is_installed(a, "project", root) if root else False
        parts = [label for ok, label in ((g, "user-wide"), (p, "this project")) if ok]
        where = " + ".join(parts) or "not installed"
        f_str = "found" if found[a] else _dim("not found")
        w_str = _green(where) if (g or p) else _dim(where)
        print(f"  {_pad(a, 12)} {f_str}   {w_str}")

    wt = resolve_worktree(os.getcwd())
    print(_bold("\nThis project"))
    if not wt:
        print(f"  {_dim('not recorded (not a git repo; see `undokey track`)')}")
        return
    store = Store(wt)
    n = int(store.g(["rev-list", "--count", TIMELINE_REF]).stdout.strip()) if store.head() else 0
    turns = len(turns_from_events(store.read_events()))
    print(f"  {wt}\n  {n} checkpoints, {turns} turns  {_dim(store.dir)}")
    errs = os.path.join(undokey_home(), "errors.log")
    if os.path.exists(errs) and os.path.getsize(errs) > 0:
        print(f"  {_yellow('!')} hook errors logged in {errs}")


def _effective_head(store: Store) -> Optional[str]:
    head = store.head()
    if not head:
        return None
    e = store.entry(head)
    if e.meta.get("kind") == "restore" and e.meta.get("target"):
        return str(e.meta["target"])
    return head


def _cmd_log(args: Dict[str, Any]) -> None:
    store = _store_here()
    _require_history(store)
    n = int(args.get("n") or 30)
    entries = store.list(n)
    current = _effective_head(store)
    last_day = ""
    for e in entries:
        d = e.time.strftime("%Y-%m-%d")
        if d != last_day:
            print(_dim(f"── {d}"))
            last_day = d
        kind = e.meta.get("kind")
        padded = _pad(_trunc(e.summary, 48), 48)
        if kind == "restore":
            what = _cyan(padded)
        elif kind == "pre-restore" or e.summary.startswith("outside change"):
            what = _yellow(padded)
        else:
            what = padded
        stat = ""
        if e.files:
            plural = "s" if e.files > 1 else ""
            stat = f"{e.files} file{plural} {_green(f'+{e.insertions}')} {_red(f'-{e.deletions}')}"
        mark = _green("●") if e.id == current else " "
        print(f"{mark} {_yellow(e.id[:8])}  {_dim(e.time.strftime('%H:%M:%S'))}  {what}  {stat}")
    print(_dim("\n● = current files.  undokey diff <id> · undokey to <id> · undokey undo"))


def _cmd_diff(args: Dict[str, Any]) -> None:
    store = _store_here()
    _require_history(store)
    pos = args["_"]
    if len(pos) < 2:
        _die("usage: undokey diff <id> [--now] [--stat]")
    out = store.diff(pos[1], against="now" if args.get("now") else "parent", stat=bool(args.get("stat")), color=_use_color())
    sys.stdout.write(out or _dim("(no changes)\n"))


def _restore_and_report(store: Store, target: str, label: Optional[str] = None) -> Dict[str, str]:
    prev = _effective_head(store)
    r = store.restore(target, {"label": label} if label else None)
    suffix = f" ({label})" if label else ""
    print(f"{_green('✓')} files now match {_yellow(r['target'][:8])}{suffix}")
    if prev and prev != r["target"]:
        print(_dim(f"  changed your mind? undokey to {prev[:8]}"))
    return r


def _cmd_to(args: Dict[str, Any]) -> None:
    store = _store_here()
    _require_history(store)
    if len(args["_"]) < 2:
        _die("usage: undokey to <id>")
    _restore_and_report(store, args["_"][1])


def _cmd_undo(args: Dict[str, Any]) -> None:
    store = _store_here()
    _require_history(store)
    raw_n = args["_"][1] if len(args["_"]) > 1 else "1"
    try:
        n = int(raw_n)
    except ValueError:
        n = 0
    if n < 1:
        _die("usage: undokey undo [n]")
    cid = _effective_head(store)
    undone = []
    for i in range(n):
        assert cid is not None
        e = store.entry(cid)
        if not e.parent:
            _die(
                "already at the first checkpoint; nothing to undo."
                if i == 0
                else f"only {i} step(s) available to undo."
            )
        undone.append(e)
        cid = e.parent
    for e in undone:
        print(_dim(f"  undo {e.id[:8]} {e.summary}"))
    assert cid is not None
    _restore_and_report(store, cid)


def _cmd_turns() -> None:
    store = _store_here()
    turns = turns_from_events(store.read_events())
    if not turns:
        print(_dim("No agent turns recorded yet for this project."))
        return
    for t in turns:
        status = "" if t["completed"] else _yellow(" (unfinished)")
        ts = datetime.fromtimestamp(t["startedAt"] / 1000).strftime("%H:%M:%S")
        prompt = re.sub(r"\s+", " ", t["prompt"]) if t["prompt"] else _dim("(no prompt captured)")
        print(
            f"{_bold(_pad('#' + str(t['index']), 5))}{_pad(t['agent'], 12)}{_dim(ts)}  "
            f"{_pad(str(t['tools']) + ' tools', 9)} {_trunc(prompt, 60)}{status}"
        )
    print(_dim("\nundokey rewind <#> [--after] [--chat]"))


def _find_turn(turns: List[Dict[str, Any]], ref: str) -> Optional[Dict[str, Any]]:
    s = str(ref).lstrip("#")
    if s.isdigit() and 1 <= int(s) <= len(turns):
        return turns[int(s) - 1]
    for t in turns:
        if t["turn"] == s or t["turn"].startswith(s):
            return t
    return None


def _cmd_rewind(args: Dict[str, Any]) -> None:
    store = _store_here()
    turns = turns_from_events(store.read_events())
    if len(args["_"]) < 2:
        _die("usage: undokey rewind <turn#|turnId> [--after] [--chat]")
    t = _find_turn(turns, args["_"][1])
    if not t:
        _die(f"turn {args['_'][1]} not found (see `undokey turns`).")
    where = "after" if args.get("after") else "before"
    target = t["before"] if where == "before" else t["after"]
    if not target:
        _die("no checkpoint recorded for that turn.")

    fork: Optional[Dict[str, Any]] = None
    if args.get("chat"):
        if t["agent"] != "codex" or t.get("synthetic"):
            if t["agent"] == "claude":
                _die("Claude Code can rewind its own conversation: use /rewind in Claude Code (undokey restores files).")
            _die(f"--chat is currently supported for Codex only (this turn is from {t['agent']}).")
        try:
            fork = fork_codex_session(t["transcript"], t["turn"], where)
        except Exception as e:
            _die(str(e))

    _restore_and_report(store, target, f"{where} turn #{t['index']}")
    if fork:
        print(f"{_green('✓')} conversation forked to {where} turn #{t['index']} (original session untouched)")
        print(f"  continue with: {_cyan('codex resume ' + fork['new_id'])}")


def _cmd_snapshot(args: Dict[str, Any]) -> None:
    d = os.path.abspath(args["_"][1]) if len(args["_"]) > 1 else os.getcwd()
    wt = resolve_worktree(d) or (os.path.realpath(d) if os.path.exists(d) else None)
    if not wt:
        _die(f"{d} does not exist")
    store = Store(wt)
    label = str(args["label"]) if args.get("label") and args["label"] is not True else "manual checkpoint"
    r = store.snapshot_locked(label, {"kind": "baseline" if label == "baseline" else "manual"})
    if not args.get("quiet"):
        if r["changed"]:
            print(f"{_green('✓')} checkpoint {_yellow(r['commit'][:8])}")
        else:
            print(_dim(f"unchanged ({r['commit'][:8]})"))


def _fmt_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024
    return f"{n} B"


def _cmd_ui(args: Dict[str, Any]) -> None:
    store = _store_here()
    _require_history(store)
    n = int(args.get("n") or 200)
    run_tui(store, limit=n)


def _cmd_gc(args: Dict[str, Any]) -> None:
    import shutil

    if args.get("all") or args.get("prune-missing"):
        stores = list_stores()
        if not stores:
            print(_dim("No shadow repositories found."))
            return
        total_before = 0
        total_after = 0
        removed = 0
        for item in stores:
            wt = item["worktree"]
            if not os.path.exists(wt):
                if args.get("prune-missing"):
                    shutil.rmtree(item["dir"], ignore_errors=True)
                    removed += 1
                    print(f"{_yellow('✓')} pruned missing worktree  {_dim(wt)}")
                else:
                    print(f"{_dim('skipped missing worktree (pass --prune-missing to remove):')} {wt}")
                continue
            s = Store(wt)
            r = s.gc()
            total_before += r["before"]
            total_after += r["after"]
            print(f"{_green('✓')} {_fmt_bytes(r['before'])} -> {_fmt_bytes(r['after'])}  {_dim(wt)}")
        print(_dim(f"\nTotal: {_fmt_bytes(total_before)} -> {_fmt_bytes(total_after)}" + (f", pruned {removed}" if removed else "")))
        return

    store = _store_here()
    _require_history(store)
    r = store.gc()
    print(f"{_green('✓')} compacted shadow store: {_fmt_bytes(r['before'])} -> {_fmt_bytes(r['after'])}")


def _cmd_track(args: Dict[str, Any], on: bool) -> None:
    d = os.path.realpath(os.path.abspath(args["_"][1]) if len(args["_"]) > 1 else os.getcwd())
    cfg = read_config()
    cfg["track"] = [p for p in cfg["track"] if p != d]
    if on:
        cfg["track"].append(d)
    write_config(cfg)
    print(f"{_green('✓')} {'recording' if on else 'no longer recording'} {d}")


def main(argv: Optional[Sequence[str]] = None) -> None:
    args = _parse_args(list(sys.argv[1:] if argv is None else argv))
    cmd = args["_"][0] if args["_"] else None
    try:
        if args.get("version") or cmd == "version":
            print(__version__)
        elif not cmd or args.get("help") or cmd == "help":
            print(_help_text())
        elif cmd == "hook":
            agent = args["_"][1] if len(args["_"]) > 1 else "unknown"
            raw = sys.stdin.read()
            out = handle_hook(agent, raw)
            if out:
                sys.stdout.write(out)
            raise SystemExit(0)
        elif cmd == "init":
            _cmd_init(args)
        elif cmd == "uninstall":
            _cmd_uninstall(args)
        elif cmd in ("status", "doctor"):
            _cmd_status()
        elif cmd in ("ui", "browse"):
            _cmd_ui(args)
        elif cmd == "log":
            _cmd_log(args)
        elif cmd in ("diff", "show"):
            _cmd_diff(args)
        elif cmd in ("to", "restore"):
            _cmd_to(args)
        elif cmd == "undo":
            _cmd_undo(args)
        elif cmd == "turns":
            _cmd_turns()
        elif cmd == "rewind":
            _cmd_rewind(args)
        elif cmd == "snapshot":
            _cmd_snapshot(args)
        elif cmd == "gc":
            _cmd_gc(args)
        elif cmd == "track":
            _cmd_track(args, True)
        elif cmd == "untrack":
            _cmd_track(args, False)
        else:
            _die(f'unknown command "{cmd}". Run `undokey help`.')
    except SystemExit:
        raise
    except Exception as e:
        if cmd == "hook":
            raise SystemExit(0)
        _die(str(e))
