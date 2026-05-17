"""File Browser for Cardputer-Adv.

Browse /flash/, open directories, view text files, and delete files.
Navigation is confined to /flash/.

Keys:
  ; / , / w    scroll up
  . / / / s    scroll down
  Enter        open directory or view file
  D            delete selected file (Y/N confirmation)
  ESC / Q      go up one level / exit
"""

import os
import time

import M5
import machine
from hardware import MatrixKeyboard


_BLACK    = 0x000000
_ORANGE   = 0xCC785C
_CREAM    = 0xF0EEE6
_DARK     = 0x1F1F1F
_GRAY_MID = 0x777777
_GREEN    = 0x00FF00
_RED      = 0xFF0000

_LCD = M5.Lcd
_W   = 240
_H   = 135

_ROOT        = "/flash"
_MAX_VISIBLE = 8
_ROW_H       = 12
_CONTENT_Y   = 18
_HINT_H      = 16
_HINT_Y      = _H - _HINT_H


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass


def _draw_header(title):
    _LCD.fillRect(0, 0, _W, 16, _DARK)
    _LCD.fillRect(0, 16, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    t = title
    while len(t) > 1 and _LCD.textWidth(t) > _W - 8:
        t = t[:-1]
    if t != title:
        t = t[:-2] + ".."
    _LCD.drawString(t, 4, 3)


def _draw_hint(text):
    _LCD.fillRect(0, _HINT_Y, _W, _HINT_H, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    x = max((_W - _LCD.textWidth(text)) // 2, 4)
    _LCD.drawString(text, x, _HINT_Y + 3)


def _hbytes(n):
    if n >= 1_048_576:
        return "{:.1f}M".format(n / 1_048_576)
    if n >= 1024:
        return "{:.0f}K".format(n / 1024)
    return "{}B".format(n)


def _list_dir(path):
    entries = []
    try:
        for name in os.listdir(path):
            full = path + "/" + name
            try:
                st = os.stat(full)
                is_dir = bool(st[0] & 0x4000)
                entries.append((name, is_dir, st[6]))
            except Exception:
                entries.append((name, False, 0))
    except Exception:
        pass
    dirs  = sorted([e for e in entries if     e[1]], key=lambda x: x[0].lower())
    files = sorted([e for e in entries if not e[1]], key=lambda x: x[0].lower())
    return dirs + files


def _draw_list(path, entries, cursor, scroll_top):
    _LCD.fillScreen(_BLACK)
    rel = path[len(_ROOT):] or "/"
    _draw_header("Files: " + rel)

    y = _CONTENT_Y + 1
    visible = entries[scroll_top:scroll_top + _MAX_VISIBLE]

    for i, (name, is_dir, size) in enumerate(visible):
        selected = (scroll_top + i) == cursor

        if selected:
            _LCD.fillRect(0, y - 1, _W, _ROW_H - 1, _ORANGE)
            fg, bg = _BLACK, _ORANGE
        else:
            fg = _ORANGE if is_dir else _CREAM
            bg = _BLACK

        label    = (name + "/") if is_dir else name
        size_str = "" if is_dir else _hbytes(size)
        size_w   = (_LCD.textWidth(size_str) + 6) if size_str else 0
        max_w    = _W - 8 - size_w

        display = label
        while len(display) > 1 and _LCD.textWidth(display) > max_w:
            display = display[:-1]
        if display != label:
            display = display[:-2] + ".."

        _LCD.setTextSize(1)
        _LCD.setTextColor(fg, bg)
        _LCD.drawString(display, 4, y)

        if size_str:
            sz_fg = _DARK if selected else _GRAY_MID
            _LCD.setTextColor(sz_fg, bg)
            _LCD.drawString(size_str, _W - size_w + 2, y)

        y += _ROW_H

    # Scroll arrows
    if scroll_top > 0:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("^", _W - 10, _CONTENT_Y + 1)
    if scroll_top + _MAX_VISIBLE < len(entries):
        last_y = _CONTENT_Y + 1 + (min(len(entries) - scroll_top, _MAX_VISIBLE) - 1) * _ROW_H
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("v", _W - 10, last_y)

    if not entries:
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        msg = "(empty)"
        _LCD.setTextSize(1)
        _LCD.drawString(msg, (_W - _LCD.textWidth(msg)) // 2, _CONTENT_Y + 32)
        _draw_hint("Q back")
    else:
        _draw_hint(";. nav  Enter open  D del  Q back")


def _read_lines(path, max_bytes=4096):
    try:
        with open(path, "r") as f:
            text = f.read(max_bytes)
    except Exception as e:
        return None, str(e)
    lines = []
    for raw in text.split("\n"):
        # wrap long lines at ~38 chars (approx 6px/char at DejaVu9)
        while len(raw) > 38:
            lines.append(raw[:38])
            raw = raw[38:]
        lines.append(raw)
    return lines, None


def _draw_viewer(path, lines, scroll_top, max_lines):
    _LCD.fillScreen(_BLACK)
    _draw_header(path.split("/")[-1])
    _LCD.setTextSize(1)
    y = _CONTENT_Y + 1
    for i in range(max_lines):
        idx = scroll_top + i
        if idx >= len(lines):
            break
        _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(lines[idx], 2, y)
        y += 11
    _draw_hint(";. scroll  Q back")


def _draw_confirm(message):
    _LCD.fillRect(20, 45, 200, 42, _DARK)
    _LCD.fillRect(20, 45, 200, 1, _ORANGE)
    _LCD.fillRect(20, 86, 200, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_CREAM, _DARK)
    mw = _LCD.textWidth(message)
    _LCD.drawString(message, 20 + (200 - mw) // 2, 53)
    prompt = "Y confirm  N cancel"
    _LCD.setTextColor(_ORANGE, _DARK)
    pw = _LCD.textWidth(prompt)
    _LCD.drawString(prompt, 20 + (200 - pw) // 2, 69)


def _key_intent(k):
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D):
            return "enter"
        if k == 0x1B:
            return "back"
        if k in (0x08, 0x7F):
            return "bs"
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return None
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    if ch in (";", ",", "w"):
        return "up"
    if ch in (".", "/", "s"):
        return "down"
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "q":
        return "back"
    if ch == "d":
        return "delete"
    if ch == "y":
        return "yes"
    if ch == "n":
        return "no"
    return None


_LINE_H   = 11
_MAX_LINES = (_HINT_Y - _CONTENT_Y) // _LINE_H


def _browse(kb, path):
    cursor     = 0
    scroll_top = 0
    entries    = _list_dir(path)
    needs_redraw = True

    while True:
        if needs_redraw:
            _draw_list(path, entries, cursor, scroll_top)
            needs_redraw = False

        kb.tick()
        intent = _key_intent(kb.get_key())

        if intent == "back":
            return

        elif intent == "up" and entries:
            cursor = (cursor - 1) % len(entries)
            if cursor < scroll_top:
                scroll_top = cursor
            elif cursor >= scroll_top + _MAX_VISIBLE:
                scroll_top = max(0, len(entries) - _MAX_VISIBLE)
            needs_redraw = True

        elif intent == "down" and entries:
            cursor = (cursor + 1) % len(entries)
            if cursor >= scroll_top + _MAX_VISIBLE:
                scroll_top = cursor - _MAX_VISIBLE + 1
            elif cursor < scroll_top:
                scroll_top = 0
            needs_redraw = True

        elif intent == "enter" and entries:
            name, is_dir, _ = entries[cursor]
            full = path + "/" + name

            if is_dir:
                _browse(kb, full)
                entries = _list_dir(path)
                cursor     = min(cursor, max(0, len(entries) - 1))
                scroll_top = min(scroll_top, max(0, len(entries) - _MAX_VISIBLE))
                needs_redraw = True
            else:
                lines, err = _read_lines(full)
                if err:
                    _LCD.fillScreen(_BLACK)
                    _draw_header(name)
                    _LCD.setTextSize(1)
                    _LCD.setTextColor(_RED, _BLACK)
                    _LCD.drawString("Cannot read:", 4, _CONTENT_Y + 10)
                    _LCD.setTextColor(_GRAY_MID, _BLACK)
                    _LCD.drawString(err[:36], 4, _CONTENT_Y + 24)
                    _draw_hint("any key back")
                    time.sleep_ms(300)
                    while True:
                        kb.tick()
                        if kb.get_key() is not None:
                            break
                        time.sleep_ms(40)
                else:
                    vscroll = 0
                    _draw_viewer(full, lines, vscroll, _MAX_LINES)
                    while True:
                        kb.tick()
                        vi = _key_intent(kb.get_key())
                        if vi == "back":
                            break
                        elif vi == "up" and vscroll > 0:
                            vscroll -= 1
                            _draw_viewer(full, lines, vscroll, _MAX_LINES)
                        elif vi == "down" and vscroll + _MAX_LINES < len(lines):
                            vscroll += 1
                            _draw_viewer(full, lines, vscroll, _MAX_LINES)
                        time.sleep_ms(40)
                needs_redraw = True

        elif intent == "delete" and entries:
            name, is_dir, _ = entries[cursor]
            if not is_dir:
                full = path + "/" + name
                short = name if len(name) <= 24 else name[:22] + ".."
                _draw_confirm("Delete {}?".format(short))
                while True:
                    kb.tick()
                    ci = _key_intent(kb.get_key())
                    if ci == "yes":
                        try:
                            os.remove(full)
                        except Exception:
                            pass
                        entries    = _list_dir(path)
                        cursor     = min(cursor, max(0, len(entries) - 1))
                        scroll_top = min(scroll_top, max(0, len(entries) - _MAX_VISIBLE))
                        break
                    elif ci in ("no", "back"):
                        break
                    time.sleep_ms(40)
                needs_redraw = True

        time.sleep_ms(40)


def run():
    _set_font()
    kb = MatrixKeyboard()
    time.sleep_ms(400)
    _browse(kb, _ROOT)


try:
    run()
finally:
    try:
        _LCD.fillScreen(_BLACK)
    except Exception:
        pass
    time.sleep_ms(200)
    machine.reset()
