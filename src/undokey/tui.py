from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

from undokey.store import Checkpoint, Store


class TuiState:
    """Pure state & diff-cache for the interactive checkpoint browser (testable without a TTY)."""

    def __init__(self, store: Store, limit: int = 200) -> None:
        self.store = store
        self.limit = limit
        self.entries: List[Checkpoint] = []
        self.current_id: Optional[str] = None
        self.selected: int = 0
        self.list_offset: int = 0
        self.diff_offset: int = 0
        self.diff_mode: str = "parent"  # "parent" | "now"
        self.confirm_restore: bool = False
        self.message: str = ""
        self._diff_cache: Dict[Tuple[str, str], List[str]] = {}
        self.reload()

    def reload(self) -> None:
        self.entries = self.store.list(self.limit)
        head = self.store.head()
        if head:
            e = self.store.entry(head)
            self.current_id = str(e.meta["target"]) if e.meta.get("kind") == "restore" and e.meta.get("target") else head
        else:
            self.current_id = None
        self._diff_cache.clear()
        if self.selected >= len(self.entries):
            self.selected = max(0, len(self.entries) - 1)
        self.diff_offset = 0
        self.confirm_restore = False

    @property
    def selected_entry(self) -> Optional[Checkpoint]:
        if 0 <= self.selected < len(self.entries):
            return self.entries[self.selected]
        return None

    def move(self, delta: int) -> None:
        if not self.entries:
            return
        nxt = max(0, min(len(self.entries) - 1, self.selected + delta))
        if nxt != self.selected:
            self.selected = nxt
            self.diff_offset = 0
            self.confirm_restore = False

    def toggle_diff_mode(self) -> None:
        self.diff_mode = "now" if self.diff_mode == "parent" else "parent"
        self.diff_offset = 0

    def current_diff_lines(self) -> List[str]:
        entry = self.selected_entry
        if not entry:
            return []
        key = (entry.id, self.diff_mode)
        if key not in self._diff_cache:
            raw = self.store.diff(entry.id, against=self.diff_mode, color=False)
            self._diff_cache[key] = raw.splitlines() if raw else ["(no changes)"]
        return self._diff_cache[key]

    def scroll_diff(self, delta: int, page_height: int = 20) -> None:
        lines = self.current_diff_lines()
        max_off = max(0, len(lines) - max(1, page_height))
        self.diff_offset = max(0, min(max_off, self.diff_offset + delta))

    def restore_selected(self) -> Optional[Dict[str, str]]:
        entry = self.selected_entry
        if not entry:
            return None
        res = self.store.restore(entry.id, {"via": "ui"})
        self.message = f"Restored files to {res['target'][:8]} (undoable — select previous entry to revert)"
        self.reload()
        return res

    def undo_one(self) -> Optional[Dict[str, str]]:
        if not self.current_id:
            return None
        cur = self.store.entry(self.current_id)
        if not cur.parent:
            self.message = "Already at the first checkpoint; nothing to undo."
            return None
        res = self.store.restore(cur.parent, {"via": "ui-undo"})
        self.message = f"Undid {cur.id[:8]} -> files now match {res['target'][:8]}"
        self.reload()
        return res


def _fit(text: str, width: int) -> str:
    if width <= 0:
        return ""
    out: List[str] = []
    w = 0
    for ch in text:
        cw = 2 if ord(ch) > 0x2E80 else 1
        if w + cw > width:
            if width >= 2 and out:
                out[-1] = "…"
            break
        out.append(ch)
        w += cw
    return "".join(out)


def _safe_add(win: "object", y: int, x: int, text: str, width: int, attr: int = 0) -> None:
    if width <= 0 or y < 0:
        return
    try:
        win.addnstr(y, x, _fit(text, width), width, attr)  # type: ignore[attr-defined]
    except Exception:
        pass


def run_tui(store: Store, limit: int = 200) -> None:
    """Launch the interactive curses checkpoint browser."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise RuntimeError("`undokey ui` requires an interactive terminal (use `undokey log` / `undokey diff` instead).")
    try:
        import curses
    except ImportError as e:
        raise RuntimeError(
            "`curses` is not available in this Python build (on Windows, run `pip install windows-curses`, or use `undokey log`)."
        ) from e

    state = TuiState(store, limit=limit)
    if not state.entries:
        raise RuntimeError(f"nothing recorded yet for {store.worktree}. Run `undokey init`, then use your agent as usual.")

    def _loop(stdscr: "curses.window") -> None:
        curses.curs_set(0)
        stdscr.keypad(True)
        has_color = False
        if curses.has_colors():
            curses.start_color()
            try:
                curses.use_default_colors()
                bg = -1
            except Exception:
                bg = curses.COLOR_BLACK
            curses.init_pair(1, curses.COLOR_GREEN, bg)   # + added / current dot
            curses.init_pair(2, curses.COLOR_RED, bg)     # - removed
            curses.init_pair(3, curses.COLOR_CYAN, bg)    # @@ hunk / restore
            curses.init_pair(4, curses.COLOR_YELLOW, bg)  # hash / file header / warning
            curses.init_pair(5, curses.COLOR_BLACK, curses.COLOR_CYAN)  # header / selection
            has_color = True

        def cp(n: int) -> int:
            return curses.color_pair(n) if has_color else 0

        repo_name = os.path.basename(store.worktree)

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()
            if h < 8 or w < 30:
                _safe_add(stdscr, 0, 0, "Terminal too small (need >= 30x8). Press q to quit.", max(1, w - 1))
                stdscr.refresh()
                ch = stdscr.getch()
                if ch in (ord("q"), ord("Q"), 27):
                    break
                continue

            # Header
            mode_label = "diff: vs now" if state.diff_mode == "now" else "diff: step"
            header = f" undokey · {repo_name} · {len(state.entries)} checkpoints   [{mode_label}]"
            _safe_add(stdscr, 0, 0, header.ljust(w), w, cp(5) | curses.A_BOLD)

            # Layout: side-by-side when wide (>= 96 cols), stacked otherwise
            body_h = h - 2
            side_by_side = w >= 96
            if side_by_side:
                list_w = max(40, min(56, int(w * 0.44)))
                list_h = body_h
                diff_x = list_w + 1
                diff_y = 1
                diff_w = w - diff_x
                diff_h = body_h
            else:
                list_w = w
                list_h = max(4, body_h // 2)
                diff_x = 0
                diff_y = 1 + list_h + 1
                diff_w = w
                diff_h = max(1, body_h - list_h - 1)

            # Adjust scroll offset for list
            if state.selected < state.list_offset:
                state.list_offset = state.selected
            elif state.selected >= state.list_offset + list_h:
                state.list_offset = state.selected - list_h + 1

            # Draw checkpoint list
            for row in range(list_h):
                idx = state.list_offset + row
                if idx >= len(state.entries):
                    break
                e = state.entries[idx]
                is_sel = idx == state.selected
                is_cur = e.id == state.current_id
                dot = "●" if is_cur else " "
                stat = f"+{e.insertions}/-{e.deletions}" if e.files else ""
                prefix = f"{dot} {e.id[:8]} {e.time.strftime('%H:%M:%S')} "
                avail = max(4, list_w - len(prefix) - (len(stat) + 1 if stat else 0))
                line = f"{prefix}{_fit(e.summary, avail)}"
                if stat and len(line) + len(stat) + 1 <= list_w:
                    line = line + " " * (list_w - len(line) - len(stat)) + stat
                if is_sel:
                    _safe_add(stdscr, 1 + row, 0, line.ljust(list_w), list_w, curses.A_REVERSE | curses.A_BOLD)
                else:
                    attr = cp(3) if e.meta.get("kind") == "restore" else (cp(1) if is_cur else 0)
                    _safe_add(stdscr, 1 + row, 0, line, list_w, attr)

            # Divider
            if side_by_side:
                for row in range(body_h):
                    _safe_add(stdscr, 1 + row, list_w, "│", 1, curses.A_DIM)
            else:
                div = f"── diff ({mode_label}) " + "─" * max(0, w - 24)
                _safe_add(stdscr, 1 + list_h, 0, div, w, curses.A_DIM)

            # Draw diff preview
            diff_lines = state.current_diff_lines()
            for row in range(diff_h):
                di = state.diff_offset + row
                if di >= len(diff_lines):
                    break
                ln = diff_lines[di]
                if ln.startswith("+++") or ln.startswith("---") or ln.startswith("diff --git"):
                    attr = cp(4) | curses.A_BOLD
                elif ln.startswith("@@"):
                    attr = cp(3)
                elif ln.startswith("+"):
                    attr = cp(1)
                elif ln.startswith("-"):
                    attr = cp(2)
                else:
                    attr = 0
                _safe_add(stdscr, diff_y + row, diff_x, ln, diff_w, attr)

            # Footer / prompt
            if state.confirm_restore and state.selected_entry:
                se = state.selected_entry
                footer = f" Restore files to {se.id[:8]} ({_fit(se.summary, 36)})? [y/N] "
                _safe_add(stdscr, h - 1, 0, footer.ljust(w), w, cp(4) | curses.A_REVERSE | curses.A_BOLD)
            elif state.message:
                _safe_add(stdscr, h - 1, 0, f" {state.message}".ljust(w), w, cp(1) | curses.A_BOLD)
            else:
                hint = " ↑/↓ j/k move · Tab/n diff vs now · [/] scroll diff · Enter restore · u undo · q quit"
                _safe_add(stdscr, h - 1, 0, hint, w, curses.A_DIM)

            stdscr.refresh()
            ch = stdscr.getch()
            state.message = ""

            if state.confirm_restore:
                if ch in (ord("y"), ord("Y")):
                    state.restore_selected()
                else:
                    state.confirm_restore = False
                    state.message = "Restore cancelled."
                continue

            if ch in (ord("q"), ord("Q"), 27):
                break
            if ch in (curses.KEY_DOWN, ord("j")):
                state.move(1)
            elif ch in (curses.KEY_UP, ord("k")):
                state.move(-1)
            elif ch in (curses.KEY_HOME, ord("g")):
                state.move(-len(state.entries))
            elif ch in (curses.KEY_END, ord("G")):
                state.move(len(state.entries))
            elif ch in (ord("\t"), ord("n"), ord("N")):
                state.toggle_diff_mode()
            elif ch in (ord("]"), curses.KEY_NPAGE, 4):  # ] / PgDn / Ctrl-D
                state.scroll_diff(max(1, diff_h // 2), diff_h)
            elif ch in (ord("["), curses.KEY_PPAGE, 21):  # [ / PgUp / Ctrl-U
                state.scroll_diff(-max(1, diff_h // 2), diff_h)
            elif ch in (ord("u"), ord("U")):
                state.undo_one()
            elif ch in (10, 13, curses.KEY_ENTER):
                if state.selected_entry:
                    state.confirm_restore = True

    curses.wrapper(_loop)
