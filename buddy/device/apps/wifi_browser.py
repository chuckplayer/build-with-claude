"""WiFi Browser for Cardputer-Adv.

Scan for available networks, pick one with the arrow keys, type a
password (skipped for open networks), connect, and save the credential
to /flash/wifi_saved.json so main.py auto-connects on next boot.

Navigation:
  ; / w        scroll up
  . / s        scroll down
  Enter        select network / confirm password
  Backspace    delete last char in password field
  ESC / Q      back to previous screen / exit to launcher
"""

import json
import time

import M5
import machine
import network
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

_SAVE_PATH    = "/flash/wifi_saved.json"
_MAX_VISIBLE  = 5
_ROW_H        = 16
_CONTENT_Y    = 22   # first pixel below the orange hairline
_HINT_H       = 18
_HINT_Y       = _H - _HINT_H
_CONNECT_TIMEOUT_MS = 12000


# ---- helpers ----------------------------------------------------------------

def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass


def _draw_header(title):
    _LCD.fillRect(0, 0, _W, 20, _DARK)
    _LCD.fillRect(0, 20, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString(title, 6, 5)


def _draw_hint(text):
    _LCD.fillRect(0, _HINT_Y, _W, _HINT_H, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    x = (_W - _LCD.textWidth(text)) // 2
    _LCD.drawString(text, max(x, 4), _HINT_Y + 4)


def _signal_filled(rssi):
    """Number of filled bars (0–4) for an RSSI value."""
    if rssi >= -55: return 4
    if rssi >= -65: return 3
    if rssi >= -75: return 2
    if rssi >= -85: return 1
    return 0


def _draw_signal(x, y, rssi):
    """Four staircase bars at (x, y), orange if filled, dark if empty."""
    filled = _signal_filled(rssi)
    heights = (3, 5, 7, 9)
    for i, h in enumerate(heights):
        color = _ORANGE if i < filled else _DARK
        bx = x + i * 4
        by = y + (9 - h)
        _LCD.fillRect(bx, by, 3, h, color)


def _key_intent(k):
    """Return 'up', 'down', 'enter', 'back', 'bs', printable char, or None."""
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
    if 0x20 <= ord(k[0]) <= 0x7E:
        return k[0]   # printable — return the original char (preserving case)
    return None


# ---- scan -------------------------------------------------------------------

def _scan():
    """Activate STA, scan, deduplicate by SSID, sort by RSSI descending.

    Returns (networks, error_str).  Each network is (ssid, rssi, authmode).
    authmode 0 = open; anything else = requires password.
    """
    _LCD.fillScreen(_BLACK)
    _draw_header("WiFi Browser")
    _LCD.setTextSize(1)
    _LCD.setTextColor(_CREAM, _BLACK)
    msg = "Scanning for networks..."
    _LCD.drawString(msg, (_W - _LCD.textWidth(msg)) // 2, 52)
    _LCD.setTextColor(_GRAY_MID, _BLACK)
    note = "(takes a few seconds)"
    _LCD.drawString(note, (_W - _LCD.textWidth(note)) // 2, 72)

    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
    time.sleep_ms(300)

    try:
        raw = sta.scan()
    except Exception as e:
        return [], "scan error: {}".format(e)

    # Deduplicate by SSID, keep the entry with the best (highest) RSSI.
    best = {}
    for entry in raw:
        ssid_b, _, _, rssi, authmode, _ = entry[0], entry[1], entry[2], entry[3], entry[4], entry[5]
        try:
            ssid = ssid_b.decode("utf-8") if isinstance(ssid_b, bytes) else str(ssid_b)
        except Exception:
            continue
        ssid = ssid.strip()
        if not ssid:
            continue
        if ssid not in best or rssi > best[ssid][1]:
            best[ssid] = (ssid, rssi, authmode)

    networks = sorted(best.values(), key=lambda n: n[1], reverse=True)
    return networks, None


# ---- screens ----------------------------------------------------------------

def _draw_list(networks, cursor, scroll_top):
    _LCD.fillScreen(_BLACK)
    _draw_header("WiFi  ({} found)".format(len(networks)))

    y = _CONTENT_Y + 2
    visible = networks[scroll_top:scroll_top + _MAX_VISIBLE]
    for i, (ssid, rssi, authmode) in enumerate(visible):
        abs_i = scroll_top + i
        selected = abs_i == cursor
        locked = authmode != 0

        if selected:
            _LCD.fillRect(0, y - 1, _W - 28, _ROW_H - 2, _ORANGE)
            _LCD.setTextColor(_BLACK, _ORANGE)
        else:
            _LCD.setTextColor(_CREAM, _BLACK)

        # Truncate SSID to leave room for signal + lock indicators on the right.
        max_w = _W - 44
        display = ssid
        while len(display) > 1 and _LCD.textWidth(display) > max_w:
            display = display[:-1]
        if display != ssid:
            display = display[:-2] + ".."
        _LCD.setTextSize(1)
        _LCD.drawString(display, 4, y)

        # Signal bars (always drawn against black, independent of highlight).
        _draw_signal(_W - 24, y + 1, rssi)

        # Lock indicator — a small "L" in orange for secured networks.
        if locked:
            _LCD.setTextColor(_ORANGE, _BLACK)
            _LCD.drawString("L", _W - 8, y)

        y += _ROW_H

    # Scroll arrows at the right edge when there are items beyond the viewport.
    if scroll_top > 0:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("^", _W - 10, _CONTENT_Y + 2)
    if scroll_top + _MAX_VISIBLE < len(networks):
        last_row_y = _CONTENT_Y + 2 + (min(len(networks) - scroll_top, _MAX_VISIBLE) - 1) * _ROW_H
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("v", _W - 10, last_row_y)

    _draw_hint(";. scroll  Enter pick  Q exit")


def _draw_password(ssid, password, error=None):
    _LCD.fillScreen(_BLACK)
    _draw_header("Enter Password")
    _LCD.setTextSize(1)

    _LCD.setTextColor(_GRAY_MID, _BLACK)
    ssid_d = ssid if _LCD.textWidth(ssid) < 200 else ssid[:22] + ".."
    _LCD.drawString("Network: " + ssid_d, 6, 28)

    _LCD.setTextColor(_CREAM, _BLACK)
    _LCD.drawString("Password:", 6, 48)

    # Password box — show what the user has typed so they can verify.
    box_y = 62
    _LCD.fillRect(4, box_y, _W - 8, 18, _DARK)
    _LCD.setTextColor(_ORANGE, _DARK)
    # Show at most the trailing 27 characters so long passwords stay visible.
    display = password[-27:] if len(password) > 27 else password
    _LCD.drawString(display + "_", 8, box_y + 4)

    if error:
        _LCD.setTextColor(_RED, _BLACK)
        _LCD.drawString((error)[:34], 6, 86)

    _draw_hint("Enter connect  ESC back  BSP delete")


def _draw_connecting(ssid):
    _LCD.fillScreen(_BLACK)
    _draw_header("Connecting")
    _LCD.setTextSize(1)
    _LCD.setTextColor(_CREAM, _BLACK)
    msg = "Connecting to:"
    _LCD.drawString(msg, (_W - _LCD.textWidth(msg)) // 2, 38)
    _LCD.setTextColor(_ORANGE, _BLACK)
    s = ssid if _LCD.textWidth(ssid) < _W - 20 else ssid[:26] + ".."
    _LCD.drawString(s, (_W - _LCD.textWidth(s)) // 2, 56)
    _LCD.setTextColor(_GRAY_MID, _BLACK)
    note = "please wait..."
    _LCD.drawString(note, (_W - _LCD.textWidth(note)) // 2, 76)


def _draw_result(ok, ssid, ip=None, err=None):
    _LCD.fillScreen(_BLACK)
    _draw_header("WiFi Browser")
    _LCD.setTextSize(1)
    if ok:
        _LCD.setTextColor(_GREEN, _BLACK)
        head = "Connected!"
        _LCD.drawString(head, (_W - _LCD.textWidth(head)) // 2, 30)
        _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(ssid, (_W - _LCD.textWidth(ssid)) // 2, 50)
        if ip:
            ip_str = "IP: " + ip
            _LCD.setTextColor(_GRAY_MID, _BLACK)
            _LCD.drawString(ip_str, (_W - _LCD.textWidth(ip_str)) // 2, 68)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        note = "saved for next boot"
        _LCD.drawString(note, (_W - _LCD.textWidth(note)) // 2, 88)
    else:
        _LCD.setTextColor(_RED, _BLACK)
        head = "Connection failed"
        _LCD.drawString(head, (_W - _LCD.textWidth(head)) // 2, 30)
        if err:
            _LCD.setTextColor(_GRAY_MID, _BLACK)
            _LCD.drawString(err[:32], (_W - _LCD.textWidth(err[:32])) // 2, 52)
    _draw_hint("any key  back to list")


# ---- connect + save ---------------------------------------------------------

def _connect(ssid, password):
    """Connect to ssid. Returns (ok, ip_or_err_string)."""
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
    # Disconnect first so we don't inherit a stale association.
    if sta.isconnected():
        sta.disconnect()
        time.sleep_ms(300)
    try:
        sta.connect(ssid, password)
    except Exception as e:
        return False, str(e)
    t0 = time.ticks_ms()
    while not sta.isconnected():
        if time.ticks_diff(time.ticks_ms(), t0) > _CONNECT_TIMEOUT_MS:
            return False, "no IP after {}s".format(_CONNECT_TIMEOUT_MS // 1000)
        time.sleep_ms(200)
    return True, sta.ifconfig()[0]


def _save(ssid, password):
    try:
        with open(_SAVE_PATH, "w") as f:
            json.dump({"ssid": ssid, "password": password}, f)
    except Exception as e:
        print("wifi_browser: save error:", e)


# ---- main loop --------------------------------------------------------------

def run():
    _set_font()
    kb = MatrixKeyboard()
    time.sleep_ms(400)

    while True:  # outer: re-scan on request

        # Scan ----------------------------------------------------------------
        networks, scan_err = _scan()

        if not networks:
            _LCD.fillScreen(_BLACK)
            _draw_header("WiFi Browser")
            _LCD.setTextSize(1)
            _LCD.setTextColor(_RED, _BLACK)
            msg = scan_err if scan_err else "No networks found"
            _LCD.drawString(msg, (_W - _LCD.textWidth(msg)) // 2, 52)
            _draw_hint("Enter rescan  Q exit")
            while True:
                kb.tick()
                intent = _key_intent(kb.get_key())
                if intent == "back":
                    return
                if intent == "enter":
                    break
                time.sleep_ms(40)
            continue

        # List navigation -----------------------------------------------------
        cursor     = 0
        scroll_top = 0
        needs_redraw = True

        while True:  # inner: navigate list until a network is chosen or exit
            if needs_redraw:
                _draw_list(networks, cursor, scroll_top)
                needs_redraw = False

            kb.tick()
            intent = _key_intent(kb.get_key())

            if intent == "back":
                return

            elif intent == "up":
                cursor = (cursor - 1) % len(networks)
                if cursor < scroll_top:
                    scroll_top = cursor
                elif cursor >= scroll_top + _MAX_VISIBLE:
                    scroll_top = max(0, len(networks) - _MAX_VISIBLE)
                needs_redraw = True

            elif intent == "down":
                cursor = (cursor + 1) % len(networks)
                if cursor >= scroll_top + _MAX_VISIBLE:
                    scroll_top = cursor - _MAX_VISIBLE + 1
                elif cursor < scroll_top:
                    scroll_top = 0
                needs_redraw = True

            elif intent == "enter":
                ssid, _rssi, authmode = networks[cursor]
                locked  = authmode != 0
                password = ""

                # Password entry (open networks skip straight to connect) -----
                if locked:
                    go_back = False
                    _draw_password(ssid, password)
                    while True:
                        kb.tick()
                        pw_intent = _key_intent(kb.get_key())
                        if pw_intent == "back":
                            go_back = True
                            break
                        elif pw_intent == "enter":
                            break
                        elif pw_intent == "bs":
                            if password:
                                password = password[:-1]
                                _draw_password(ssid, password)
                        elif pw_intent is not None and len(pw_intent) == 1:
                            password += pw_intent
                            _draw_password(ssid, password)
                        time.sleep_ms(40)

                    if go_back:
                        needs_redraw = True
                        continue  # back to list loop

                # Connect ------------------------------------------------------
                _draw_connecting(ssid)
                ok, result = _connect(ssid, password)

                if ok:
                    _save(ssid, password)

                _draw_result(ok, ssid,
                             ip=result if ok else None,
                             err=result if not ok else None)

                # Wait for any key, then decide what to do next.
                time.sleep_ms(400)
                while True:
                    kb.tick()
                    if kb.get_key() is not None:
                        break
                    time.sleep_ms(40)

                if ok:
                    return   # success — exit, finally block does machine.reset()

                # Failed — go back to the list (same scan results, no rescan).
                needs_redraw = True

            time.sleep_ms(40)


try:
    run()
finally:
    try:
        _LCD.fillScreen(_BLACK)
    except Exception:
        pass
    time.sleep_ms(200)
    machine.reset()
