"""Custom launcher for the Cardputer-Adv buddy bundle.

Why this exists: UIFlow 2.0's stock launcher (startup/cardputeradv/apps/
app_list.py) runs a background BLE advertise for flow.m5stack.com
pairing before handing control to a user app. On this ESP32-S3 build,
once that advertise has run the NimBLE controller rejects any
subsequent ``gap_advertise(adv_data=...)`` call with OSError -519
("Memory Capacity Exceeded"), regardless of payload shape, until
reboot. The result is that our BLE peripherals (Claude Buddy) fall
back to empty advertising — discoverable only to permissive scanners
like LightBlue and invisible to iOS / the desktop Claude Buddy app.

The fix is to skip UIFlow's launcher entirely: set the NVS
``boot_option`` to 2 ("user app mode") so UIFlow's boot.py calls
``/flash/main.py`` instead of starting its framework, and have
``main.py`` show our own menu that hands off to the selected app
without touching BLE. UIFlow's BLE code never runs, the stack stays
pristine, and our adv_data payload works on first try.

Menu items are the three ``.py`` files in ``/flash/apps/``. Selection
is driven by the matrix keyboard — arrow keys (``;`` up / ``.`` down,
matching the Cardputer-Adv's labeled arrow cluster) scroll the
highlight, Enter launches. The launched app exits via
``machine.reset()`` (same pattern every Buddy-bundle app uses), which
reboots the device and brings us back to this launcher cleanly — no
"return from app" protocol to maintain.

Layout mirrors the app suite: 20 px DARK header, ORANGE hairline,
cream-on-black menu rows, hint strip at the bottom. Consistent visual
rhythm so the launcher feels like part of the bundle.
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


# Burst animation (Claude-orange starburst, 16 frames at 72x72). Lives
# as a peer module at /flash/burst_frames.py. Import is wrapped so the
# launcher still works on a board where someone forgot to push the
# frames file — it just won't animate.
try:
    import burst_frames as _burst
except ImportError as e:
    print("launcher: burst_frames not available:", e)
    _burst = None


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
            # (keyboard) or BLE after this function returns.
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


# Make peer modules (buddy_ble, buddy_ui_cp, etc.) at /flash/ importable
# so the launched apps can `import buddy_ble` without each one repeating
# the sys.path dance. This matches what claude_buddy.py already does
# defensively; doing it centrally here is cleaner than spreading the
# fix across every entrypoint.
for _p in ("/flash", "/flash/apps"):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception as e:
        # Build without FONTS; fall back to default. Not fatal.
        print("launcher: setFont fallback:", e)


# Module-level reference so the BLE singleton survives past
# _init_ble's frame. bluetooth.BLE() returns the same global
# instance on every call so technically dropping the reference is
# fine, but keeping it visible makes the lifetime obvious.
_ble = None


def _init_ble():
    """Bring up NimBLE up early, before WiFi, for clean coexistence.

    No advertising or service registration here — that's the
    application layer's job (buddy_ble._ensure_stack). All we want
    is to flip the controller into the active state while the radio
    is still idle, so the coexistence arbiter sees BLE as a
    registered subsystem before WiFi starts asking for slots.

    The C-fault we used to hit at `active(True)` only happened when
    the radio was already busy with WiFi traffic. Calling it here,
    on a freshly-booted chip, is reliable in the testing we did.
    If that ever stops being true the failure mode is a launcher
    crash on boot — bad, but at least visible (the chip will boot-
    loop and the serial log will show this print sequence).
    """
    global _ble
    try:
        import bluetooth
        print("launcher: bringing up NimBLE")
        _ble = bluetooth.BLE()
        if not _ble.active():
            _ble.active(True)
        # Short settle so the controller is fully up before any
        # later code (WiFi, app launch) starts contending for the
        # radio. Same 250 ms we use in buddy_ble._ensure_stack.
        time.sleep_ms(250)
        print("launcher: NimBLE active")
    except Exception as e:
        # Defensive — if the import or active call somehow raises a
        # Python-level error (rather than C-faulting the chip), the
        # launcher should still come up. The downstream effect is
        # that claude_buddy will then try the active(True) call
        # itself and fall back to the old fragile path; other apps
        # don't care.
        print("launcher: NimBLE init warning:", e)


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


def _wifi_pip_label():
    """Header-strip text + color for the current WiFi state. Returns
    ``(text, color)``. Colors are picked to read cleanly against the
    DARK header background.
    """
    if _wifi_status is None or not _wifi_status.get("ok"):
        return ("OFFLINE", _GRAY_MID)
    return ("ONLINE", _GREEN)


def _discover_apps():
    """Return a sorted list of ``(display_name, module_basename)``.

    Module basename is the filename without extension (for import).
    Display name is the same but with underscores turned into spaces
    and title-cased — gives a slightly friendlier menu than raw
    filenames without forcing us to ship a separate metadata file.
    """
    try:
        files = sorted(
            f for f in os.listdir(_APPS_DIR) if f.endswith(".py")
        )
    except OSError as e:
        print("launcher: cannot list", _APPS_DIR, e)
        return []
    out = []
    for fname in files:
        mod = fname[:-3]
        # Skip dunder / private files defensively — nothing in the
        # bundle today uses them, but a future .py dropped in for a
        # helper shouldn't land in the visible menu.
        if mod.startswith("_"):
            continue
        display = mod.replace("_", " ")
        out.append((display, mod))
    return out


# Layout: the burst animation (72x72) lives in the right portion of
# the content area; the menu occupies the left. Leave a small gap
# between them so the orange highlight on the selected menu row
# doesn't touch the animation's bounding box.
_MENU_X = 10
_MENU_RIGHT = 150         # menu highlight ends here; animation starts beyond
_MAX_VISIBLE = 6          # rows shown at once; drives scroll viewport
_BURST_W = 72
_BURST_H = 72
_BURST_X = 160            # top-left x of burst bounding box
_BURST_Y = 24             # top-left y (just below the header hairline at y=16)
_BURST_CX = _BURST_X + _BURST_W // 2
_BURST_CY = _BURST_Y + _BURST_H // 2


def _draw_burst_frame(frame_idx):
    """Draw one frame of the orange starburst into the right region.

    Each frame is a flat bytes object of (y, x, length) triples
    describing horizontal runs of opaque orange pixels on a black
    background. We clear the bounding box once (so last frame's
    spokes don't ghost) then issue one fillRect per run.

    Silently no-ops if ``burst_frames`` wasn't importable — the
    launcher still renders the menu + hints without the animation.
    """
    if _burst is None:
        return
    data = _burst.FRAMES[frame_idx % len(_burst.FRAMES)]
    color = _burst.COLOR
    _LCD.fillRect(_BURST_X, _BURST_Y, _BURST_W, _BURST_H, _BLACK)
    i = 0
    n = len(data)
    while i < n:
        ry = data[i]
        rx = data[i + 1]
        rl = data[i + 2]
        _LCD.fillRect(_BURST_X + rx, _BURST_Y + ry, rl, 1, color)
        i += 3


def _draw_chrome(apps, cursor, scroll_top=0):
    """Full repaint of chrome + menu (NOT the burst animation — that
    ticks on its own cadence in the main loop). Fast enough to just
    redraw on cursor move; at 240x135 the whole buffer is small and
    the panel push takes a few ms."""
    _LCD.fillScreen(_BLACK)

    # Status bar header — 16 px tall, no title text, just WiFi + battery.
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

    # Menu rows constrained to the left region so the burst animation
    # has clean space on the right. Only _MAX_VISIBLE rows are shown at
    # once; scroll_top is the index of the first visible app.
    y = 22
    row_h = 16
    hi_x = 4
    hi_w = _MENU_RIGHT - hi_x        # highlight width, ends before burst
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
    hint = "; . up/down   Enter launch"
    _LCD.drawString(hint, (_W - _LCD.textWidth(hint)) // 2, _H - 14)

    # Paint the initial burst frame so the animation region isn't
    # just a black square until the first tick fires.
    _draw_burst_frame(0)


def _intent(k):
    """Normalize a MatrixKeyboard return to up / down / launch / None.

    The Cardputer-Adv's arrow cluster is four keys with arrow glyphs
    silk-screened on them, but they report as their unshifted ASCII:
    ``;`` (labeled up), ``,`` (labeled left), ``.`` (labeled down),
    and ``/`` (labeled right). In a vertical menu, left/right don't
    really have a meaning — users intuitively reach for the
    physically-arrow-labeled keys regardless of direction and expect
    the menu to scroll. So we accept all four as up/down: the two
    "upper-ish" keys (``;`` and ``,``) scroll up, the two "lower-ish"
    keys (``.`` and ``/``) scroll down. WASD is also accepted for
    gamepad-muscle-memory users.

    Enter reports as ``0x0A`` (LF) on this firmware build, not
    ``0x0D`` (CR). We accept both so a future build that flips back
    to CR doesn't silently break the launcher.
    """
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D):
            return "launch"
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
    return None


def _launch(mod_name):
    """Import the module, which runs its entrypoint at import time
    (every app in the bundle has a ``run()`` at module bottom — see
    claude_buddy.py / snake.py / hello_cardputer.py). On clean exit
    the app calls ``machine.reset()`` which brings us back here."""
    _LCD.fillScreen(_BLACK)
    try:
        __import__(mod_name)
    except Exception as e:
        # App crashed during import/run. Show a minimal error screen
        # so we're not just blank, wait for the user to press any
        # key, then come back to the menu.
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
        # Drop the half-imported module from sys.modules. Without
        # this, a second selection of the same app does nothing:
        # __import__ sees the cached entry and returns immediately
        # without re-running the module body, so the app's run()
        # never fires and the user is left staring at a black
        # screen. Idempotent — KeyError just means the failure
        # happened before the partial entry was installed.
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

    # IMPORTANT: initialise the keyboard BEFORE any radio activity (BLE/WiFi).
    # The matrix IC must be constructed while the radio is idle; constructing
    # it after a failed WiFi attempt leaves the ESP32 background-retrying,
    # which produces the same "permanently stuck returning None" symptom as
    # constructing it too early on cold boot. The 800 ms cold-boot delay still
    # applies; BLE + WiFi init below serve as more-than-sufficient extra time.
    time.sleep_ms(800)
    kb = MatrixKeyboard()
    # 400 ms flush so any Enter bounce from the previous app's machine.reset()
    # decays before BLE/WiFi start.
    time.sleep_ms(400)

    # Bring up NimBLE BEFORE connecting to WiFi. ESP32's 2.4 GHz
    # radio is shared between WiFi and BLE through a software
    # coexistence arbiter; ESP-IDF documents that controllers
    # initialized in BT-first order coexist far more reliably than
    # the reverse. We learned this the hard way — claude_buddy was
    # C-faulting at `bluetooth.BLE().active(True)` whenever it
    # tried to bring BLE up after WiFi was already running, and no
    # amount of "tear WiFi down first" worked because ESP32's WiFi
    # shutdown doesn't fully release the radio back to BLE in this
    # firmware. Doing it once here, while the radio is idle, gets
    # NimBLE registered with the coexistence arbiter; subsequent
    # `_ensure_stack` calls in claude_buddy see pre_active=True
    # and skip the fault-prone transition.
    _init_ble()
    # Connect to the event WiFi BEFORE the launcher menu so the user
    # sees the connect status as part of boot rather than a sudden
    # screen swap mid-menu. Splash takes ~3-9 s in the success case
    # (connect + 1.5 s status flash) and at most ~9.5 s on failure
    # (8 s timeout + 1.5 s flash). Long but explicit.
    _connect_wifi_with_splash()

    apps = _discover_apps()
    if not apps:
        _LCD.fillScreen(_BLACK)
        _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString("No apps in " + _APPS_DIR, 6, 40)
        while True:
            time.sleep_ms(500)

    cursor = 0
    scroll_top = 0
    _draw_chrome(apps, cursor, scroll_top)

    # Drain keypresses that accumulated while the WiFi splash was showing.
    for _ in range(15):
        kb.tick()
        kb.get_key()
        time.sleep_ms(20)

    # Burst animation state. We tick one frame per FRAME_MS on top of
    # the 40 ms keyboard poll — so ~every other iteration advances a
    # frame. The burst region is disjoint from the menu/chrome, so we
    # never need to repaint the menu just because the animation
    # advanced; we only repaint the burst's own bounding box.
    frame = 0
    frame_ms = _burst.FRAME_MS if _burst is not None else 80
    last_frame_ms = time.ticks_ms()

    while True:
        kb.tick()
        intent = _intent(kb.get_key())
        if intent == "up":
            cursor = (cursor - 1) % len(apps)
            if cursor < scroll_top:
                scroll_top = cursor
            elif cursor >= scroll_top + _MAX_VISIBLE:
                # Wrapped from first item to last — show the tail end.
                scroll_top = max(0, len(apps) - _MAX_VISIBLE)
            _draw_chrome(apps, cursor, scroll_top)
        elif intent == "down":
            cursor = (cursor + 1) % len(apps)
            if cursor >= scroll_top + _MAX_VISIBLE:
                scroll_top = cursor - _MAX_VISIBLE + 1
            elif cursor < scroll_top:
                # Wrapped from last item to first — show the top.
                scroll_top = 0
            _draw_chrome(apps, cursor, scroll_top)
        elif intent == "launch":
            _, mod_name = apps[cursor]
            _launch(mod_name)
            # If _launch returns (error path), redraw menu. Reset the
            # burst phase so the animation restarts from frame 0 for
            # visual consistency with a fresh launcher entry.
            _draw_chrome(apps, cursor, scroll_top)
            frame = 0
            last_frame_ms = time.ticks_ms()
            # Debounce so the user's release of Enter doesn't re-fire.
            time.sleep_ms(300)

        # Advance the burst animation if it's time. time.ticks_diff
        # handles wrap-around safely (ticks_ms rolls over every ~9
        # hours on MicroPython; not a real concern on a launcher but
        # cheap insurance).
        now = time.ticks_ms()
        if time.ticks_diff(now, last_frame_ms) >= frame_ms:
            frame += 1
            _draw_burst_frame(frame)
            last_frame_ms = now

        time.sleep_ms(40)


# UIFlow's boot.py invokes us by running this file rather than
# calling a function. The previous if/else with both arms calling
# main() was self-cancelling — the comment claimed the guard
# protected against import-time auto-run, but the else branch
# defeated that. Run bare; that's what we actually want.
main()
