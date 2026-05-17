"""Timer for Cardputer-Adv — countdown and stopwatch in one app.

Keys (always):
  T        toggle mode (not allowed while countdown is running)
  ESC / Q  exit to launcher

Countdown keys:
  0–9      shift digits in (calculator-style: newest digit on right)
  Backspace  shift digits out (delete right-to-left)
  Enter    start (from setup) / pause / resume
  R        reset to setup

Stopwatch keys:
  Enter    start / pause / resume
  R        reset to zero
"""

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

_MODE_COUNTDOWN = 0
_MODE_STOPWATCH = 1

_CD_SETUP   = 0
_CD_RUNNING = 1
_CD_PAUSED  = 2
_CD_DONE    = 3

_SW_IDLE    = 0
_SW_RUNNING = 1
_SW_PAUSED  = 2


# ---- helpers ----------------------------------------------------------------

def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass


def _draw_header(mode):
    _LCD.fillRect(0, 0, _W, 16, _DARK)
    _LCD.fillRect(0, 16, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString("Timer", 6, 3)
    badge = "COUNTDOWN" if mode == _MODE_COUNTDOWN else "STOPWATCH"
    _LCD.setTextColor(_GRAY_MID, _DARK)
    _LCD.drawString(badge, _W - _LCD.textWidth(badge) - 6, 3)


def _draw_hint(text):
    _LCD.fillRect(0, _H - 16, _W, 16, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    x = max((_W - _LCD.textWidth(text)) // 2, 4)
    _LCD.drawString(text, x, _H - 12)


def _clear_content():
    _LCD.fillRect(0, 17, _W, _H - 17 - 16, _BLACK)


def _draw_time(text, color):
    """Large centered time string in the content area."""
    _LCD.setTextSize(3)
    w = _LCD.textWidth(text)
    _LCD.fillRect(0, 26, _W, 32, _BLACK)
    _LCD.setTextColor(color, _BLACK)
    _LCD.drawString(text, (_W - w) // 2, 27)
    _LCD.setTextSize(1)


def _draw_status(text, color):
    _LCD.fillRect(0, 62, _W, 14, _BLACK)
    _LCD.setTextSize(1)
    x = max((_W - _LCD.textWidth(text)) // 2, 4)
    _LCD.setTextColor(color, _BLACK)
    _LCD.drawString(text, x, 63)


def _beep_done():
    """Three rising tones when countdown hits zero."""
    try:
        spk = M5.Speaker
        for freq, dur in ((880, 150), (1100, 150), (1320, 250)):
            spk.tone(freq, dur)
            time.sleep_ms(dur + 60)
    except Exception:
        pass


# ---- formatters -------------------------------------------------------------

def _fmt_cd(total_ms):
    s = max(0, total_ms) // 1000
    return "{:02d}:{:02d}:{:02d}".format(s // 3600, (s % 3600) // 60, s % 60)


def _fmt_sw(total_ms):
    cs = max(0, total_ms) // 10
    m  = cs // 6000
    cs = cs % 6000
    return "{:02d}:{:02d}.{:02d}".format(m, cs // 100, cs % 100)


# ---- screen painters --------------------------------------------------------

def _draw_cd_setup(digits):
    _clear_content()
    h = digits[0] * 10 + digits[1]
    m = digits[2] * 10 + digits[3]
    s = digits[4] * 10 + digits[5]
    _draw_time("{:02d}:{:02d}:{:02d}".format(h, m, s), _ORANGE)
    _draw_status("type digits  Enter to start", _GRAY_MID)
    _draw_hint("0-9 set  BSP clear  Enter start  T stopwatch  Q exit")


def _draw_countdown(state, remaining_ms):
    _clear_content()
    if state == _CD_DONE:
        _draw_time("00:00:00", _RED)
        _draw_status("TIME'S UP!", _RED)
        _draw_hint("R reset  T stopwatch  Q exit")
    elif state == _CD_PAUSED:
        _draw_time(_fmt_cd(remaining_ms), _ORANGE)
        _draw_status("PAUSED", _GRAY_MID)
        _draw_hint("Enter resume  R reset  T stopwatch  Q exit")
    else:
        _draw_time(_fmt_cd(remaining_ms), _GREEN)
        _draw_status("RUNNING", _GREEN)
        _draw_hint("Enter pause  R reset  Q exit")


def _draw_stopwatch(state, elapsed_ms):
    _clear_content()
    if state == _SW_IDLE:
        color = _CREAM
        _draw_time(_fmt_sw(elapsed_ms), color)
        _draw_status("ready", _GRAY_MID)
        _draw_hint("Enter start  T countdown  Q exit")
    elif state == _SW_RUNNING:
        _draw_time(_fmt_sw(elapsed_ms), _GREEN)
        _draw_status("RUNNING", _GREEN)
        _draw_hint("Enter pause  R reset  Q exit")
    else:
        _draw_time(_fmt_sw(elapsed_ms), _ORANGE)
        _draw_status("PAUSED", _GRAY_MID)
        _draw_hint("Enter resume  R reset  T countdown  Q exit")


# ---- input ------------------------------------------------------------------

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
    if ch in ("\r", "\n"):
        return "enter"
    if ch == "q":
        return "back"
    if ch == "r":
        return "reset"
    if ch == "t":
        return "toggle"
    if k[0].isdigit():
        return k[0]
    return None


def _digits_to_ms(digits):
    h = digits[0] * 10 + digits[1]
    m = digits[2] * 10 + digits[3]
    s = digits[4] * 10 + digits[5]
    return (h * 3600 + m * 60 + s) * 1000


# ---- main loop --------------------------------------------------------------

def run():
    _set_font()
    kb = MatrixKeyboard()
    time.sleep_ms(400)

    mode = _MODE_COUNTDOWN

    # Countdown state
    cd_digits    = [0, 0, 0, 0, 0, 0]
    cd_state     = _CD_SETUP
    cd_remaining = 0
    cd_deadline  = 0   # ticks_ms target

    # Stopwatch state
    sw_state   = _SW_IDLE
    sw_elapsed = 0     # accumulated ms
    sw_start   = 0     # ticks_ms of current run start

    last_tick = time.ticks_ms()

    _LCD.fillScreen(_BLACK)
    _draw_header(mode)
    _draw_cd_setup(cd_digits)

    while True:
        kb.tick()
        intent = _key_intent(kb.get_key())
        now    = time.ticks_ms()

        # ---- universal ----
        if intent == "back":
            return

        # ---- mode toggle ----
        if intent == "toggle":
            if mode == _MODE_COUNTDOWN and cd_state != _CD_RUNNING:
                mode = _MODE_STOPWATCH
                _LCD.fillScreen(_BLACK)
                _draw_header(mode)
                _draw_stopwatch(sw_state, sw_elapsed)
                last_tick = now
            elif mode == _MODE_STOPWATCH and sw_state != _SW_RUNNING:
                mode = _MODE_COUNTDOWN
                _LCD.fillScreen(_BLACK)
                _draw_header(mode)
                if cd_state == _CD_SETUP:
                    _draw_cd_setup(cd_digits)
                else:
                    _draw_countdown(cd_state, cd_remaining)
                last_tick = now

        # ---- countdown ----
        elif mode == _MODE_COUNTDOWN:

            if cd_state == _CD_SETUP:
                if intent is not None and len(str(intent)) == 1 and str(intent).isdigit():
                    cd_digits = cd_digits[1:] + [int(intent)]
                    _draw_cd_setup(cd_digits)
                elif intent == "bs":
                    cd_digits = [0] + cd_digits[:-1]
                    _draw_cd_setup(cd_digits)
                elif intent == "enter":
                    ms = _digits_to_ms(cd_digits)
                    if ms > 0:
                        cd_remaining = ms
                        cd_deadline  = time.ticks_add(now, ms)
                        cd_state     = _CD_RUNNING
                        last_tick    = now
                        _draw_countdown(cd_state, cd_remaining)
                elif intent == "reset":
                    cd_digits = [0, 0, 0, 0, 0, 0]
                    _draw_cd_setup(cd_digits)

            elif cd_state == _CD_RUNNING:
                if intent == "enter":
                    cd_state = _CD_PAUSED
                    _draw_countdown(cd_state, cd_remaining)
                elif intent == "reset":
                    cd_state  = _CD_SETUP
                    cd_digits = [0, 0, 0, 0, 0, 0]
                    _LCD.fillScreen(_BLACK)
                    _draw_header(mode)
                    _draw_cd_setup(cd_digits)
                else:
                    # Tick every 100 ms for smooth display.
                    if time.ticks_diff(now, last_tick) >= 100:
                        last_tick    = now
                        cd_remaining = max(0, time.ticks_diff(cd_deadline, now))
                        if cd_remaining == 0:
                            cd_state = _CD_DONE
                            _draw_countdown(cd_state, 0)
                            _beep_done()
                        else:
                            _draw_countdown(cd_state, cd_remaining)

            elif cd_state == _CD_PAUSED:
                if intent == "enter":
                    cd_deadline = time.ticks_add(now, cd_remaining)
                    cd_state    = _CD_RUNNING
                    last_tick   = now
                    _draw_countdown(cd_state, cd_remaining)
                elif intent == "reset":
                    cd_state  = _CD_SETUP
                    cd_digits = [0, 0, 0, 0, 0, 0]
                    _LCD.fillScreen(_BLACK)
                    _draw_header(mode)
                    _draw_cd_setup(cd_digits)

            elif cd_state == _CD_DONE:
                if intent == "reset":
                    cd_state  = _CD_SETUP
                    cd_digits = [0, 0, 0, 0, 0, 0]
                    _LCD.fillScreen(_BLACK)
                    _draw_header(mode)
                    _draw_cd_setup(cd_digits)

        # ---- stopwatch ----
        elif mode == _MODE_STOPWATCH:

            if sw_state == _SW_IDLE:
                if intent == "enter":
                    sw_start   = now
                    sw_elapsed = 0
                    sw_state   = _SW_RUNNING
                    last_tick  = now
                    _draw_stopwatch(sw_state, 0)

            elif sw_state == _SW_RUNNING:
                total = sw_elapsed + time.ticks_diff(now, sw_start)
                if intent == "enter":
                    sw_elapsed = total
                    sw_state   = _SW_PAUSED
                    _draw_stopwatch(sw_state, sw_elapsed)
                elif intent == "reset":
                    sw_state   = _SW_IDLE
                    sw_elapsed = 0
                    _draw_stopwatch(sw_state, 0)
                else:
                    if time.ticks_diff(now, last_tick) >= 50:
                        last_tick = now
                        _draw_stopwatch(sw_state, total)

            elif sw_state == _SW_PAUSED:
                if intent == "enter":
                    sw_start  = now
                    sw_state  = _SW_RUNNING
                    last_tick = now
                    _draw_stopwatch(sw_state, sw_elapsed)
                elif intent == "reset":
                    sw_state   = _SW_IDLE
                    sw_elapsed = 0
                    _draw_stopwatch(sw_state, 0)

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
