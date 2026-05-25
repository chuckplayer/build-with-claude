"""Custom launcher for the Cardputer-Adv buddy bundle.

Why this exists: UIFlow 2.0's stock launcher runs its own framework
before handing control to a user app.  We skip it entirely by setting
the NVS ``boot_option`` to 2 ("user app mode") so UIFlow's boot.py
calls ``/flash/main.py`` directly.  This gives us full control over
which apps appear in the menu — useful for curating the experience and
keeping helper modules (system_info, file_browser, wifi_browser) out
of the top-level list without hiding them completely.

Menu items are the ``.py`` files in ``/flash/apps/``.  Selection is
driven by the matrix keyboard — ``;`` / ``.`` scroll up/down,
Enter launches.  The launched app exits via ``machine.reset()``, which
reboots the device and returns to this launcher.

The top-level menu shows user-facing apps plus a **Utilities** submenu
entry.  Selecting Utilities reveals system_info, file_browser, and
wifi_browser; ESC (or Q) returns to the top level.

Layout: 20 px DARK header with WiFi + battery status, ORANGE hairline,
cream-on-black menu rows, hint strip at the bottom.
"""

# Note: MicroPython on this UIFlow 2.0 build doesn't ship __future__,
# so no `from __future__ import annotations`. Keep type hints as
# strings if we need them (we don't here).

import json as _json
import os
import sys
import time

import M5
import machine
from hardware import MatrixKeyboard


# boot_option=2 skips UIFlow's framework entirely, which means
# M5.begin() has already run in boot.py but the framework hasn't
# set up any input/display glue. Call M5.begin() defensively in
# case we're re-entered via a soft reset that didn't rerun boot.py.
# It's idempotent — a second call is a no-op if the hardware is
# already initialized.
try:
    M5.begin()
except Exception as e:
    print("launcher: M5.begin() warning:", e)


# Event-WiFi auto-connect lives in a peer module so the credentials
# (which are intentionally checked into the public repo for the
# event bundle) are easy to find and replace post-event.
try:
    import wifi_event as _wifi
except ImportError as e:
    print("launcher: wifi_event not available:", e)
    _wifi = None


def _load_saved_wifi():
    """Return (ssid, password) from /flash/wifi_saved.json, or (None, None)."""
    try:
        with open("/flash/wifi_saved.json") as f:
            d = _json.load(f)
            ssid = d.get("ssid")
            if ssid:
                return ssid, d.get("password", "")
    except Exception:
        pass
    return None, None


def _raw_connect(ssid, password, timeout_ms):
    """Connect to ssid/password and return a result dict.

    Same shape as wifi_event.connect() so the splash renderer can treat
    both sources identically.
    """
    import network
    sta = network.WLAN(network.STA_IF)
    if not sta.active():
        sta.active(True)
    if sta.isconnected():
        info = sta.ifconfig()
        return {"ok": True, "ssid": ssid, "ip": info[0], "elapsed_ms": 0}
    # Disconnect any in-progress attempt before starting a fresh one.
    # Without this, a second call inherits the first's "connecting" state and
    # sta.connect() raises OSError or fails silently.
    try:
        sta.disconnect()
        time.sleep_ms(100)
    except Exception:
        pass
    t0 = time.ticks_ms()
    try:
        sta.connect(ssid, password)
    except Exception as e:
        return {"ok": False, "ssid": ssid, "err": str(e), "elapsed_ms": 0}
    while not sta.isconnected():
        elapsed = time.ticks_diff(time.ticks_ms(), t0)
        if elapsed > timeout_ms:
            # Stop the background retry so it doesn't interfere with I2C
            # (keyboard) after this function returns.
            try:
                sta.disconnect()
            except Exception:
                pass
            return {"ok": False, "ssid": ssid,
                    "err": "no IP after {}s".format(timeout_ms // 1000),
                    "elapsed_ms": elapsed}
        time.sleep_ms(200)
    info = sta.ifconfig()
    try:
        rssi = sta.status("rssi")
    except Exception:
        rssi = -60
    return {"ok": True, "ssid": ssid, "ip": info[0], "rssi": rssi,
            "elapsed_ms": time.ticks_diff(time.ticks_ms(), t0)}


_BLACK = 0x000000
_ORANGE = 0xCC785C
_CREAM = 0xF0EEE6
_DARK = 0x1F1F1F
_GRAY_MID = 0x777777
_GREEN = 0x00FF00
_RED = 0xFF0000


def _read_battery():
    """Return (level_pct, is_charging). Falls back to (None, False) on error."""
    try:
        return M5.Power.getBatteryLevel(), M5.Power.isCharging()
    except Exception:
        return None, False

# Last-known WiFi connect result, populated by _connect_wifi_with_splash
# and read by _draw_chrome to render the header status pip. None until
# the first connect attempt has run.
_wifi_status = None

_LCD = M5.Lcd
_W = 240
_H = 135

_APPS_DIR = "/flash/apps"

# Modules that belong in the Utilities submenu rather than the top level.
_UTILITIES_MODULES = ("system_info", "file_browser", "wifi_browser")

# Make peer modules at /flash/ importable so the launched apps can
# import them without each one repeating the sys.path dance.
for _p in ("/flash", "/flash/apps"):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception as e:
        # Build without FONTS; fall back to default. Not fatal.
        print("launcher: setFont fallback:", e)


def _connect_wifi_with_splash():
    """Try saved WiFi credentials first, then wifi_event fallback.

    Saved credentials (written by wifi_browser.py) get a 4 s fast-fail
    timeout so a stale public-network entry doesn't hold up the boot for
    too long when the user is back home.  The wifi_event fallback (home
    network) gets the full 8 s budget.  If the saved and event SSIDs are
    the same (user saved their home network via the browser), only one
    attempt is made with the full 8 s timeout.

    Stores the final result in ``_wifi_status`` for the header pip.
    """
    global _wifi_status

    # Build ordered attempt list: (ssid, password, timeout_ms).
    attempts = []
    saved_ssid, saved_pw = _load_saved_wifi()
    if saved_ssid:
        attempts.append((saved_ssid, saved_pw, 4000))
    if _wifi is not None:
        event_ssid = getattr(_wifi, "SSID", None)
        event_pw   = getattr(_wifi, "PASSWORD", "")
        # Only add as a separate attempt if it differs from the saved SSID.
        if event_ssid and event_ssid != saved_ssid:
            attempts.append((event_ssid, event_pw, 8000))

    if not attempts:
        return

    result = {"ok": False, "ssid": "", "err": "no credentials", "elapsed_ms": 0}
    for ssid, password, timeout_ms in attempts:
        _LCD.fillScreen(_BLACK)
        _LCD.setTextSize(1)
        _LCD.setTextColor(_ORANGE, _BLACK)
        title = "Connecting to WiFi"
        _LCD.drawString(title, (_W - _LCD.textWidth(title)) // 2, 40)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        sub = "SSID: {}".format(ssid)
        _LCD.drawString(sub, (_W - _LCD.textWidth(sub)) // 2, 60)
        tstr = "(up to {}s)".format(timeout_ms // 1000)
        _LCD.drawString(tstr, (_W - _LCD.textWidth(tstr)) // 2, 78)
        try:
            result = _raw_connect(ssid, password, timeout_ms)
        except Exception as e:
            result = {"ok": False, "ssid": ssid,
                      "err": "exception: {}".format(e), "elapsed_ms": 0}
        if result.get("ok"):
            break

    _wifi_status = result

    _LCD.fillScreen(_BLACK)
    _LCD.setTextSize(1)
    if result.get("ok"):
        _LCD.setTextColor(_GREEN, _BLACK)
        head = "Connected"
        _LCD.drawString(head, (_W - _LCD.textWidth(head)) // 2, 36)
        _LCD.setTextColor(_CREAM, _BLACK)
        ip_line = "IP: {}".format(result.get("ip", "?"))
        _LCD.drawString(ip_line, (_W - _LCD.textWidth(ip_line)) // 2, 60)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        ssid_line = "on {}".format(result.get("ssid", "?"))
        _LCD.drawString(ssid_line, (_W - _LCD.textWidth(ssid_line)) // 2, 80)
    else:
        _LCD.setTextColor(_RED, _BLACK)
        head = "WiFi: offline"
        _LCD.drawString(head, (_W - _LCD.textWidth(head)) // 2, 36)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        err = (result.get("err") or "")[:30]
        _LCD.drawString(err, (_W - _LCD.textWidth(err)) // 2, 60)
        note = "launcher continues anyway"
        _LCD.drawString(note, (_W - _LCD.textWidth(note)) // 2, 80)

    time.sleep_ms(1500)


def _discover_apps():
    """Return ``(top_level, utilities)`` — two sorted lists of
    ``(display_name, module_basename)``.

    ``utilities`` contains entries whose module basename is in
    ``_UTILITIES_MODULES``.  ``top_level`` contains the remaining
    entries sorted alphabetically, with a synthetic
    ``("Utilities", "__utilities__")`` entry appended at the end
    (only when ``utilities`` is non-empty).

    Module basename is the filename without extension (for import).
    Display name is the basename with underscores replaced by spaces
    and title-cased.
    """
    try:
        files = sorted(
            f for f in os.listdir(_APPS_DIR) if f.endswith(".py")
        )
    except OSError as e:
        print("launcher: cannot list", _APPS_DIR, e)
        return [], []
    top_level = []
    utilities = []
    for fname in files:
        mod = fname[:-3]
        # Skip dunder / private files — a helper dropped in shouldn't
        # show up in any visible menu.
        if mod.startswith("_"):
            continue
        display = " ".join(w[0].upper() + w[1:] for w in mod.split("_"))
        entry = (display, mod)
        if mod in _UTILITIES_MODULES:
            utilities.append(entry)
        else:
            top_level.append(entry)
    if utilities:
        top_level.append(("Utilities", "__utilities__"))
    return top_level, utilities


# Menu spans the full usable width of the screen.
_MENU_X = 10
_MENU_RIGHT = 236             # screen width 240 - 4 px margin
_MAX_VISIBLE = 6              # rows shown at once; drives scroll viewport


def _draw_chrome(apps, cursor, scroll_top=0, show_back=False):
    """Full repaint of chrome + menu. Fast enough to just redraw on
    every cursor move; at 240x135 the whole buffer is small."""
    _LCD.fillScreen(_BLACK)

    # Status bar header — 16 px tall, WiFi + battery.
    _LCD.fillRect(0, 0, _W, 16, _DARK)
    _LCD.fillRect(0, 16, _W, 1, _ORANGE)
    _LCD.setTextSize(1)

    # WiFi: 4 staircase bars then SSID (or "offline" in gray).
    if _wifi_status and _wifi_status.get("ok"):
        rssi   = _wifi_status.get("rssi", -60)
        ssid   = _wifi_status.get("ssid", "")
        filled = 4 if rssi >= -55 else 3 if rssi >= -65 else 2 if rssi >= -75 else 1
        for i, h in enumerate((3, 5, 7, 9)):
            color = _ORANGE if i < filled else 0x444444
            _LCD.fillRect(4 + i * 4, 16 - 3 - h, 3, h, color)
        max_w = 130
        ssid_d = ssid
        while _LCD.textWidth(ssid_d) > max_w and len(ssid_d) > 1:
            ssid_d = ssid_d[:-1]
        if ssid_d != ssid:
            ssid_d = ssid_d[:-2] + ".."
        _LCD.setTextColor(_CREAM, _DARK)
        _LCD.drawString(ssid_d, 22, 3)
    else:
        _LCD.setTextColor(_GRAY_MID, _DARK)
        _LCD.drawString("offline", 4, 3)

    # Battery: percentage + "+" when charging, color by level.
    pct, charging = _read_battery()
    if pct is not None:
        bat_color = _GREEN if pct >= 60 else _ORANGE if pct >= 20 else _RED
        bat_str   = "{}%{}".format(pct, "+" if charging else "")
        _LCD.setTextColor(bat_color, _DARK)
        _LCD.drawString(bat_str, _W - _LCD.textWidth(bat_str) - 4, 3)

    # Menu rows. Only _MAX_VISIBLE rows are shown at once; scroll_top
    # is the index of the first visible app.
    y = 22
    row_h = 16
    hi_x = 4
    hi_w = _MENU_RIGHT - hi_x        # highlight width
    visible = apps[scroll_top:scroll_top + _MAX_VISIBLE]
    for i, (display, _mod) in enumerate(visible):
        abs_i = scroll_top + i
        if abs_i == cursor:
            _LCD.fillRect(hi_x, y - 2, hi_w, row_h - 2, _ORANGE)
            _LCD.setTextColor(_BLACK, _ORANGE)
        else:
            _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(display, _MENU_X, y)
        y += row_h

    # Scroll indicators: orange ^ / v at the right edge of the first /
    # last visible row when there are more items beyond the viewport.
    ind_x = _MENU_RIGHT - 10
    if scroll_top > 0:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("^", ind_x, 22)
    if scroll_top + _MAX_VISIBLE < len(apps):
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("v", ind_x, 22 + (len(visible) - 1) * row_h)

    # Hint strip.
    _LCD.fillRect(0, _H - 18, _W, 18, _DARK)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    if show_back:
        hint = "; . up/down  Enter launch  ESC back"
    else:
        hint = "; . up/down   Enter launch"
    _LCD.drawString(hint, (_W - _LCD.textWidth(hint)) // 2, _H - 14)


def _intent(k):
    """Normalize a MatrixKeyboard return to up / down / launch / back / None.

    The Cardputer-Adv's arrow cluster reports as ASCII:
    ``;`` (labeled up), ``,`` (labeled left), ``.`` (labeled down),
    ``/`` (labeled right). We accept all four as up/down.  WASD also
    accepted.  Enter reports as ``0x0A`` (LF); we accept both LF and
    CR.  ESC (0x1B) and Q/q return "back" for the Utilities submenu.
    """
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D):
            return "launch"
        if k == 0x1B:
            return "back"
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return None
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    # Up: semicolon (up-arrow label), comma (left-arrow label), W
    if ch in (";", ",", "w"):
        return "up"
    # Down: period (down-arrow label), slash (right-arrow label), S
    if ch in (".", "/", "s"):
        return "down"
    if ch in ("\r", "\n"):
        return "launch"
    if ch in ("q", "\x1b"):  # Q or ESC (keyboard may return ESC as string)
        return "back"
    return None


def _launch(mod_name):
    """Import the module, which runs its entrypoint at import time.
    On clean exit the app calls ``machine.reset()`` which brings us
    back to main.py."""
    _LCD.fillScreen(_BLACK)
    try:
        __import__(mod_name)
    except Exception as e:
        # App crashed during import/run. Show a minimal error screen,
        # wait for any keypress, then return to the menu.
        _LCD.fillScreen(_BLACK)
        _LCD.setTextSize(1)
        _LCD.setTextColor(0xFF0000, _BLACK)
        _LCD.drawString("App crashed:", 6, 10)
        _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(mod_name, 6, 26)
        _LCD.drawString(str(e)[:34], 6, 44)
        _LCD.setTextColor(_GRAY_MID, _BLACK)
        _LCD.drawString("any key to return", 6, _H - 14)
        print("launcher: {} failed: {}".format(mod_name, e))
        # Drop the half-imported module from sys.modules so a second
        # selection re-runs the module body correctly.
        try:
            del sys.modules[mod_name]
        except KeyError:
            pass
        kb = MatrixKeyboard()
        while True:
            kb.tick()
            if kb.get_key() is not None:
                return
            time.sleep_ms(40)
    # Typical happy path: the imported module runs, then soft-resets
    # via machine.reset() in its finally block. That path doesn't
    # return here — we reboot back to main.py from the reset.


def main():
    _set_font()

    # IMPORTANT: initialise the keyboard BEFORE any radio activity (WiFi).
    # The matrix IC must be constructed while the radio is idle.
    time.sleep_ms(800)
    kb = MatrixKeyboard()
    # 400 ms flush so any Enter bounce from the previous app's machine.reset()
    # decays before WiFi starts.
    time.sleep_ms(400)

    # Connect to WiFi BEFORE the launcher menu so the user sees the
    # connect status as part of boot rather than a sudden screen swap
    # mid-menu.
    _connect_wifi_with_splash()

    top_apps, util_apps = _discover_apps()
    if not top_apps and not util_apps:
        _LCD.fillScreen(_BLACK)
        _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString("No apps in " + _APPS_DIR, 6, 40)
        while True:
            time.sleep_ms(500)

    # Two-mode state machine: "main" shows top-level entries (including
    # the synthetic Utilities row); "utilities" shows the submenu.
    mode = "main"
    main_cursor = 0
    main_scroll = 0
    util_cursor = 0
    util_scroll = 0

    active_apps = top_apps
    _draw_chrome(active_apps, main_cursor, main_scroll, show_back=False)

    # Drain keypresses that accumulated while the WiFi splash was showing.
    for _ in range(15):
        kb.tick()
        kb.get_key()
        time.sleep_ms(20)

    while True:
        kb.tick()
        intent = _intent(kb.get_key())

        # Determine current cursor/scroll for the active mode.
        if mode == "main":
            active_apps = top_apps
            cursor = main_cursor
            scroll_top = main_scroll
        else:
            active_apps = util_apps
            cursor = util_cursor
            scroll_top = util_scroll

        repaint = False

        if intent == "up":
            cursor = (cursor - 1) % len(active_apps)
            if cursor < scroll_top:
                scroll_top = cursor
            elif cursor >= scroll_top + _MAX_VISIBLE:
                # Wrapped from first item to last — show the tail end.
                scroll_top = max(0, len(active_apps) - _MAX_VISIBLE)
            repaint = True

        elif intent == "down":
            cursor = (cursor + 1) % len(active_apps)
            if cursor >= scroll_top + _MAX_VISIBLE:
                scroll_top = cursor - _MAX_VISIBLE + 1
            elif cursor < scroll_top:
                # Wrapped from last item to first — show the top.
                scroll_top = 0
            repaint = True

        elif intent == "launch":
            _, mod_name = active_apps[cursor]
            if mod_name == "__utilities__":
                if util_apps:  # defensive: guard against empty utilities list
                    mode = "utilities"
                    util_cursor = 0
                    util_scroll = 0
                    repaint = True
            else:
                _launch(mod_name)
                # If _launch returns (error path), redraw menu.
                repaint = True
                # Debounce so the user's release of Enter doesn't re-fire.
                time.sleep_ms(300)

        elif intent == "back":
            if mode == "utilities":
                mode = "main"
                repaint = True
            # "back" in main mode is a no-op.

        # Write updated cursor/scroll back to the correct mode slot.
        if mode == "main":
            main_cursor = cursor
            main_scroll = scroll_top
        else:
            util_cursor = cursor
            util_scroll = scroll_top

        if repaint:
            if mode == "main":
                _draw_chrome(top_apps, main_cursor, main_scroll, show_back=False)
            else:
                _draw_chrome(util_apps, util_cursor, util_scroll, show_back=True)

        time.sleep_ms(40)


# UIFlow's boot.py invokes us by running this file rather than
# calling a function. Run bare — that's what we actually want.
main()
