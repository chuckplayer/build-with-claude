"""System Info for Cardputer-Adv.

Single-screen snapshot of firmware, memory, storage, network, and
battery state. R to refresh, Q / ESC to exit.
"""

import gc
import os
import sys
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


def _set_font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass


def _draw_header():
    _LCD.fillRect(0, 0, _W, 16, _DARK)
    _LCD.fillRect(0, 16, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString("System Info", 6, 3)


def _draw_hint(text):
    _LCD.fillRect(0, _H - 16, _W, 16, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _DARK)
    x = max((_W - _LCD.textWidth(text)) // 2, 4)
    _LCD.drawString(text, x, _H - 12)


def _row(y, label, value, val_color=None):
    """Draw a label: value line."""
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY_MID, _BLACK)
    _LCD.drawString(label, 4, y)
    _LCD.setTextColor(val_color or _CREAM, _BLACK)
    _LCD.drawString(value, 4 + _LCD.textWidth(label), y)


def _hbytes(n):
    if n >= 1_048_576:
        return "{:.1f}MB".format(n / 1_048_576)
    if n >= 1024:
        return "{:.0f}KB".format(n / 1024)
    return "{}B".format(n)


def _mac_str(b):
    return ":".join("{:02x}".format(x) for x in b)


def _gather():
    d = {}

    # Firmware
    v = sys.version  # "3.4.0; MicroPython v1.27.0-dirty on 2026-05-15"
    try:
        # Extract "v1.27.0" from the version string
        parts = v.split("MicroPython ")
        d["upy"] = "MicroPython " + parts[1].split(" on ")[0] if len(parts) > 1 else v.split(";")[0].strip()
    except Exception:
        d["upy"] = v[:24]
    try:
        d["freq"] = "{}MHz".format(machine.freq() // 1_000_000)
    except Exception:
        d["freq"] = "?"

    # Memory
    gc.collect()
    free  = gc.mem_free()
    alloc = gc.mem_alloc()
    d["mem"] = "{} free / {} total".format(_hbytes(free), _hbytes(free + alloc))

    # Flash
    try:
        sv = os.statvfs("/flash")
        d["flash"] = "{} free / {}".format(_hbytes(sv[3] * sv[0]), _hbytes(sv[2] * sv[0]))
    except Exception:
        d["flash"] = "?"

    # WiFi
    try:
        sta = network.WLAN(network.STA_IF)
        if sta.isconnected():
            d["ssid"] = sta.config("essid")
            d["ip"]   = sta.ifconfig()[0]
            try:
                d["rssi"] = "{}dBm".format(sta.status("rssi"))
            except Exception:
                d["rssi"] = None
        else:
            d["ssid"] = None
            d["ip"]   = None
            d["rssi"] = None
        try:
            d["mac"] = _mac_str(sta.config("mac"))
        except Exception:
            d["mac"] = None
    except Exception:
        d["ssid"] = None
        d["mac"]  = None

    # Battery
    try:
        d["bat_pct"]  = M5.Power.getBatteryLevel()
        d["bat_mv"]   = M5.Power.getBatteryVoltage()
        d["bat_chrg"] = M5.Power.isCharging()
    except Exception:
        d["bat_pct"] = None

    return d


def _draw(d):
    _LCD.fillScreen(_BLACK)
    _draw_header()

    lh = 11
    y  = 20

    # Firmware
    _row(y, "", d["upy"] + "  " + d["freq"])
    y += lh

    # Memory
    _row(y, "Heap  ", d["mem"])
    y += lh

    # Flash
    _row(y, "Flash ", d["flash"])
    y += lh + 3

    # WiFi
    if d.get("ssid"):
        rssi_str = "  " + d["rssi"] if d.get("rssi") else ""
        _row(y, "WiFi  ", d["ssid"] + rssi_str, _GREEN)
        y += lh
        _row(y, "IP    ", d["ip"])
        y += lh
    else:
        _row(y, "WiFi  ", "offline", _GRAY_MID)
        y += lh

    if d.get("mac"):
        _row(y, "MAC   ", d["mac"])
        y += lh

    y += 3

    # Battery
    if d.get("bat_pct") is not None:
        pct   = d["bat_pct"]
        mv    = d["bat_mv"]
        chrg  = d["bat_chrg"]
        color = _GREEN if pct >= 60 else _RED if pct < 20 else _ORANGE
        chstr = "  charging" if chrg else ""
        _row(y, "Batt  ", "{}%  {}mV{}".format(pct, mv, chstr), color)

    _draw_hint("R refresh  Q exit")


def _intent(k):
    if k is None:
        return None
    if isinstance(k, int):
        if k == 0x1B:
            return "back"
        if 0x20 <= k <= 0x7E:
            k = chr(k)
        else:
            return None
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    if ch == "q":
        return "back"
    if ch == "r":
        return "refresh"
    return None


def run():
    _set_font()
    kb = MatrixKeyboard()
    time.sleep_ms(400)

    _draw(_gather())

    while True:
        kb.tick()
        i = _intent(kb.get_key())
        if i == "back":
            return
        if i == "refresh":
            _draw(_gather())
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
