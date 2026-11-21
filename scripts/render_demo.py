#!/usr/bin/env python3
"""Render docs/demo.gif — a crisp macOS terminal demo of undokey.

Run with:
    uv run --with pillow python3 scripts/render_demo.py
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT_GIF = ROOT / "docs" / "demo.gif"
PREVIEW_DIR = ROOT / ".demo_preview"

# Canvas & typography (Tokyo Night Dark)
W, H = 960, 440
PAD_X, PAD_TOP = 28, 56
BG_OUTER = (13, 17, 23)
BG_WIN = (22, 27, 34)
BG_BAR = (33, 38, 45)
BORDER = (48, 54, 61)
BG_SEL = (45, 79, 103)
BG_HDR = (86, 182, 194)

FG = (230, 237, 243)
DIM = (125, 133, 144)
GREEN = (63, 185, 80)
RED = (248, 81, 73)
YELLOW = (210, 153, 34)
CYAN = (88, 166, 255)
TEAL = (86, 182, 194)
BLACK = (13, 17, 23)

Span = Tuple[str, Tuple[int, int, int], bool]  # (text, color, bold)
Line = List[Span]


def load_fonts(size: int = 15) -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    for path in ("/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/SFNSMono.ttf"):
        if os.path.exists(path):
            reg = ImageFont.truetype(path, size, index=0)
            try:
                bold = ImageFont.truetype(path, size, index=1)
            except Exception:
                bold = reg
            return reg, bold
    default = ImageFont.load_default()
    return default, default  # type: ignore[return-value]


REG, BOLD = load_fonts(15)
SMALL, SMALL_B = load_fonts(13)
CHAR_W = int(REG.getlength("M"))
LINE_H = 22


def base_window(title: str = "undokey — zsh — ~/code/my-app") -> Image.Image:
    img = Image.new("RGB", (W, H), BG_OUTER)
    d = ImageDraw.Draw(img)
    m = 10
    d.rounded_rectangle((m, m, W - m, H - m), radius=12, fill=BG_WIN, outline=BORDER, width=1)
    # Title bar
    d.rounded_rectangle((m + 1, m + 1, W - m - 1, m + 36), radius=11, fill=BG_BAR)
    d.rectangle((m + 1, m + 24, W - m - 1, m + 36), fill=BG_BAR)
    d.line((m + 1, m + 36, W - m - 1, m + 36), fill=BORDER, width=1)
    # Traffic lights
    for i, col in enumerate(((255, 95, 86), (255, 189, 46), (39, 201, 63))):
        cx = m + 22 + i * 20
        cy = m + 18
        d.ellipse((cx - 6, cy - 6, cx + 6, cy + 6), fill=col)
    tw = SMALL.getlength(title)
    d.text(((W - tw) / 2, m + 10), title, font=SMALL, fill=DIM)
    return img


def draw_terminal_lines(lines: List[Line], cursor: Optional[Tuple[int, int]] = None) -> Image.Image:
    img = base_window()
    d = ImageDraw.Draw(img)
    y = PAD_TOP + 6
    for row, spans in enumerate(lines):
        x = PAD_X
        for text, col, is_bold in spans:
            font = BOLD if is_bold else REG
            d.text((x, y), text, font=font, fill=col)
            x += int(font.getlength(text))
        if cursor and cursor[0] == row:
            cx = PAD_X + cursor[1] * CHAR_W
            d.rectangle((cx, y + 2, cx + CHAR_W - 1, y + LINE_H - 3), fill=TEAL)
        y += LINE_H
    return img


def draw_tui_frame(
    selected: int,
    diff_mode: str = "step",
    confirm: bool = False,
    toast: str = "",
) -> Image.Image:
    img = base_window("undokey ui — ~/code/my-app")
    d = ImageDraw.Draw(img)
    inner_x, inner_y = 18, 52
    inner_w, inner_h = W - 36, H - 70

    # Header bar
    hdr_h = 24
    d.rectangle((inner_x, inner_y, inner_x + inner_w, inner_y + hdr_h), fill=BG_HDR)
    hdr_text = f" undokey · my-app · 4 checkpoints   [diff: {diff_mode}]"
    d.text((inner_x + 4, inner_y + 3), hdr_text, font=BOLD, fill=BLACK)

    # Split panes
    list_w = 430
    div_x = inner_x + list_w
    body_y = inner_y + hdr_h + 6
    footer_y = inner_y + inner_h - 24
    d.line((div_x, body_y - 4, div_x, footer_y - 4), fill=BORDER, width=1)

    entries = [
        ("●", "9a098637", "16:58:19", "codex · Bash", "+2/-8", False),
        (" ", "52e5ce27", "16:58:12", "codex · apply_patch", "+14/-3", False),
        (" ", "36482239", "16:56:47", "codex · apply_patch", "+22/-5", False),
        (" ", "d306f314", "16:55:02", "baseline (first checkpoint)", "", False),
    ]
    if toast:
        entries.insert(0, (" ", "7c1f0a21", "16:58:31", "restored to 52e5ce27", "+8/-2", True))
        entries[1] = (" ", "9a098637", "16:58:19", "codex · Bash", "+2/-8", False)
        entries[2] = ("●", "52e5ce27", "16:58:12", "codex · apply_patch", "+14/-3", False)

    for i, (dot, hsh, ts, label, stat, is_restore) in enumerate(entries):
        ry = body_y + i * 26
        is_sel = i == selected
        if is_sel:
            d.rectangle((inner_x, ry - 2, div_x - 6, ry + 21), fill=BG_SEL)
        x = inner_x + 6
        d.text((x, ry), dot, font=BOLD, fill=GREEN if dot == "●" else DIM)
        x += 18
        d.text((x, ry), hsh, font=BOLD if is_sel else REG, fill=YELLOW)
        x += 80
        d.text((x, ry), ts, font=REG, fill=DIM)
        x += 82
        lbl_col = FG if is_sel else (CYAN if is_restore else FG)
        shown = label if len(label) <= 21 else label[:20] + "…"
        d.text((x, ry), shown, font=BOLD if is_sel else REG, fill=lbl_col)
        if stat:
            sw = int(SMALL.getlength(stat))
            d.text((div_x - 14 - sw, ry + 2), stat, font=SMALL, fill=GREEN if "+14" in stat or "+22" in stat else RED)

    # Right pane: live diff preview
    diffs = {
        0: [
            ("diff --git a/db/pool.py b/db/pool.py", YELLOW, True),
            ("--- a/db/pool.py", YELLOW, True),
            ("+++ b/db/pool.py", YELLOW, True),
            ("@@ -12,10 +12,4 @@ class Pool:", CYAN, False),
            ("     def connect(self):", FG, False),
            ("-        self._check_tls()", RED, False),
            ("-        self._verify_schema()", RED, False),
            ("-        return self._open_safe()", RED, False),
            ("+        # TODO: temporary bypass", GREEN, False),
            ("+        return self._open_raw()", GREEN, False),
        ],
        1: [
            ("diff --git a/auth/middleware.py b/auth/middleware.py", YELLOW, True),
            ("--- a/auth/middleware.py", YELLOW, True),
            ("+++ b/auth/middleware.py", YELLOW, True),
            ("@@ -4,7 +4,12 @@ def verify_token(req):", CYAN, False),
            ("     tok = req.headers.get('Authorization')", FG, False),
            ("-    if not tok:", RED, False),
            ("-        return None", RED, False),
            ("+    if not tok or not tok.startswith('Bearer '):", GREEN, False),
            ("+        raise AuthError('missing bearer token')", GREEN, False),
            ("+    return jwt.decode(tok[7:], verify=True)", GREEN, False),
        ],
    }
    diff_lines = diffs.get(0 if selected == 0 else 1, diffs[1])
    dx = div_x + 14
    for i, (txt, col, is_b) in enumerate(diff_lines):
        d.text((dx, body_y + i * 22), txt[:54], font=BOLD if is_b else REG, fill=col)

    # Footer bar
    if confirm:
        d.rectangle((inner_x, footer_y, inner_x + inner_w, footer_y + 24), fill=YELLOW)
        d.text((inner_x + 6, footer_y + 3), " Restore files to 52e5ce27 (codex · apply_patch)? [y/N]", font=BOLD, fill=BLACK)
    elif toast:
        d.text((inner_x + 6, footer_y + 3), f" ✓ {toast}", font=BOLD, fill=GREEN)
    else:
        hint = " ↑/↓ j/k move · Tab/n diff vs now · [/] scroll · Enter restore · u undo · q quit"
        d.text((inner_x + 4, footer_y + 3), hint, font=SMALL, fill=DIM)
    return img


def prompt_line(cmd: str) -> Line:
    return [("$ ", GREEN, True), (cmd, FG, False)]


def type_cmd(frames: List[Tuple[Image.Image, int]], prior: List[Line], cmd: str) -> List[Line]:
    # Type in 2-char chunks for crisp, fast pacing
    for i in range(0, len(cmd) + 1, 2):
        part = cmd[:i]
        lines = prior + [prompt_line(part)]
        frames.append((draw_terminal_lines(lines, cursor=(len(lines) - 1, 2 + len(part))), 55))
    final = prior + [prompt_line(cmd)]
    frames.append((draw_terminal_lines(final), 220))
    return final


def build_frames() -> List[Tuple[Image.Image, int]]:
    frames: List[Tuple[Image.Image, int]] = []

    # Scene 1: undokey log
    s1: List[Line] = []
    frames.append((draw_terminal_lines([prompt_line("")], cursor=(0, 2)), 450))
    s1 = type_cmd(frames, s1, "undokey log")
    s1 += [
        [("── 2026-11-21", DIM, False)],
        [("● ", GREEN, True), ("9a098637", YELLOW, False), ("  16:58:19  ", DIM, False), ("codex · Bash                        ", FG, False), ("1 file ", FG, False), ("+2 ", GREEN, False), ("-8", RED, False)],
        [("  52e5ce27", YELLOW, False), ("  16:58:12  ", DIM, False), ("codex · apply_patch                 ", FG, False), ("2 files ", FG, False), ("+14 ", GREEN, False), ("-3", RED, False)],
        [("  36482239", YELLOW, False), ("  16:56:47  ", DIM, False), ("codex · apply_patch                 ", FG, False), ("1 file ", FG, False), ("+22 ", GREEN, False), ("-5", RED, False)],
        [("  d306f314", YELLOW, False), ("  16:55:02  ", DIM, False), ("baseline (first checkpoint)         ", FG, False), ("12 files ", FG, False), ("+410", GREEN, False)],
        [("", FG, False)],
        [("● = current files.  undokey ui · undokey diff <id> · undokey undo", DIM, False)],
        [("", FG, False)],
    ]
    frames.append((draw_terminal_lines(s1), 1700))

    # Scene 2: launch undokey ui
    s1 = type_cmd(frames, s1, "undokey ui")
    frames.append((draw_tui_frame(selected=0), 1100))
    frames.append((draw_tui_frame(selected=1), 1100))
    frames.append((draw_tui_frame(selected=1, confirm=True), 950))
    frames.append((draw_tui_frame(selected=0, toast="Restored files to 52e5ce27 (press u to undo)"), 1500))

    # Scene 3: undokey turns + rewind --chat
    s3: List[Line] = []
    s3 = type_cmd(frames, s3, "undokey turns")
    s3 += [
        [("#1   ", FG, True), ("codex       ", FG, False), ("16:56:47  ", DIM, False), ("1 tools   ", FG, False), ("refactor auth middleware", FG, False)],
        [("#2   ", FG, True), ("codex       ", FG, False), ("16:58:12  ", DIM, False), ("2 tools   ", FG, False), ("rewrite database connection pool", FG, False)],
        [("", FG, False)],
        [("undokey rewind <#> [--after] [--chat]", DIM, False)],
        [("", FG, False)],
    ]
    frames.append((draw_terminal_lines(s3), 1300))

    s3 = type_cmd(frames, s3, "undokey rewind 2 --chat")
    s3 += [
        [("✓ ", GREEN, True), ("files now match ", FG, False), ("36482239", YELLOW, False), (" (before turn #2)", FG, False)],
        [("  changed your mind? undokey to 9a098637", DIM, False)],
        [("✓ ", GREEN, True), ("conversation forked to before turn #2 (original session untouched)", FG, False)],
        [("  continue with: ", FG, False), ("codex resume 01a1fd60-91c9-7103-b7a9-4c208de58d8d", CYAN, True)],
        [("", FG, False)],
        [("$ ", GREEN, True), ("# Files & Codex chat rewound together. Your .git was never touched.", DIM, False)],
    ]
    frames.append((draw_terminal_lines(s3), 3200))
    return frames


def main() -> None:
    OUT_GIF.parent.mkdir(parents=True, exist_ok=True)
    frames = build_frames()
    imgs = [im.convert("P", palette=Image.Palette.ADAPTIVE, colors=128) for im, _ in frames]
    durations = [ms for _, ms in frames]
    imgs[0].save(
        OUT_GIF,
        save_all=True,
        append_images=imgs[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    print(f"Wrote {OUT_GIF} ({OUT_GIF.stat().st_size / 1024:.1f} KB, {len(imgs)} frames)")
    if os.environ.get("SAVE_PREVIEWS"):
        PREVIEW_DIR.mkdir(exist_ok=True)
        for idx in (8, 17, 20, len(frames) - 1):
            if idx < len(frames):
                frames[idx][0].save(PREVIEW_DIR / f"frame_{idx}.png")


if __name__ == "__main__":
    main()
