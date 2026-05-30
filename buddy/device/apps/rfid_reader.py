"""RFID Reader for Cardputer-Adv.

Requires the M5Stack RFID2 Unit (WS1850S chip, 13.56 MHz ISO-14443A)
connected to Port A (SDA=G2, SCL=G1).

Keys:
  S       save current tag to /flash/rfid/
  L       list saved tags (from scan or found screen)
  ; . w s scroll up / down (list screen)
  Enter   view selected tag (list screen)
  ESC / Q back / exit
"""

import os
import time

import M5
import machine
from machine import I2C, Pin
from hardware import MatrixKeyboard


# ── Palette ──────────────────────────────────────────────────────────────────
_BLACK  = 0x000000
_ORANGE = 0xCC785C
_CREAM  = 0xF0EEE6
_DARK   = 0x1F1F1F
_GRAY   = 0x777777
_GREEN  = 0x00FF00
_RED    = 0xFF0000
_TEAL   = 0x00BFBF

_LCD    = M5.Lcd
_W, _H  = 240, 135


# ── WS1850S / MFRC522 driver (I2C) ───────────────────────────────────────────

class _RFID:
    # Registers
    _R_COMMAND  = 0x01
    _R_COM_IRQ  = 0x04
    _R_ERROR    = 0x06
    _R_FIFO_D   = 0x09
    _R_FIFO_LVL = 0x0A
    _R_CONTROL  = 0x0C
    _R_BIT_FRM  = 0x0D
    _R_MODE     = 0x11
    _R_TX_CTL   = 0x14
    _R_TX_ASK   = 0x15
    _R_T_MODE   = 0x2A
    _R_T_PRE    = 0x2B
    _R_T_RLD_H  = 0x2C
    _R_T_RLD_L  = 0x2D
    _R_VERSION  = 0x37

    # PCD commands
    _CMD_IDLE      = 0x00
    _CMD_TRANSCEIVE = 0x0C
    _CMD_RESET     = 0x0F

    # PICC commands (ISO 14443A)
    _PICC_REQIDL  = 0x26
    _PICC_ANTICOLL = 0x93
    _PICC_HLTA    = 0x50

    def __init__(self, i2c, addr=0x28):
        self._i2c  = i2c
        self._addr = addr
        self._init()

    def _wr(self, reg, val):
        # writeto_mem issues: START, addr|W, reg, val, STOP
        self._i2c.writeto_mem(self._addr, reg, bytes([val]))

    def _rd(self, reg):
        # readfrom_mem issues: START, addr|W, reg, REPEATED-START, addr|R, val, STOP
        # The repeated-start is required by WS1850S/MFRC522 -- a plain stop+start
        # between the address write and the data read causes the chip to ignore reads.
        return self._i2c.readfrom_mem(self._addr, reg, 1)[0]

    def _set(self, reg, mask):
        self._wr(reg, self._rd(reg) | mask)

    def _clr(self, reg, mask):
        self._wr(reg, self._rd(reg) & (~mask & 0xFF))

    def _init(self):
        self._wr(self._R_COMMAND, self._CMD_RESET)
        time.sleep_ms(50)
        self._wr(self._R_T_MODE,   0x8D)
        self._wr(self._R_T_PRE,    0x3E)
        self._wr(self._R_T_RLD_H,  0x00)
        self._wr(self._R_T_RLD_L,  0x1E)
        self._wr(self._R_TX_ASK,   0x40)
        self._wr(self._R_MODE,     0x3D)
        self._set(self._R_TX_CTL,  0x03)  # antenna on

    def version(self):
        return self._rd(self._R_VERSION)

    def _transceive(self, data, tx_last_bits=0):
        """Send bytes, return (ok, rx_bytes)."""
        self._wr(self._R_COMMAND, self._CMD_IDLE)
        self._wr(self._R_COM_IRQ, 0x7F)
        self._set(self._R_FIFO_LVL, 0x80)   # flush FIFO
        for b in data:
            self._wr(self._R_FIFO_D, b)
        self._wr(self._R_BIT_FRM, tx_last_bits)
        self._wr(self._R_COMMAND, self._CMD_TRANSCEIVE)
        self._set(self._R_BIT_FRM, 0x80)    # StartSend

        deadline = time.ticks_add(time.ticks_ms(), 100)
        while True:
            irq = self._rd(self._R_COM_IRQ)
            if irq & 0x31:   # RxIRq | IdleIRq | TimerIRq → done or timed out
                break
            if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
                return False, []

        self._clr(self._R_BIT_FRM, 0x80)
        if self._rd(self._R_ERROR) & 0x1B:  # overflow/collision/parity/protocol
            return False, []

        n  = self._rd(self._R_FIFO_LVL)
        rx = [self._rd(self._R_FIFO_D) for _ in range(n)]
        return bool(rx), rx

    def request(self):
        """Return True if any idle card is present."""
        ok, data = self._transceive([self._PICC_REQIDL], tx_last_bits=0x07)
        return ok and len(data) == 2

    def anticoll(self):
        """Return (ok, uid_bytes) for the nearest card."""
        self._wr(self._R_BIT_FRM, 0x00)
        ok, data = self._transceive([self._PICC_ANTICOLL, 0x20])
        if not ok or len(data) < 5:
            return False, []
        chk = 0
        for b in data[:4]:
            chk ^= b
        if chk != data[4]:
            return False, []
        return True, data[:4]

    def halt(self):
        self._transceive([self._PICC_HLTA, 0x00])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _uid_str(uid):
    return " ".join("{:02X}".format(b) for b in uid)


def _uid_hex(uid):
    return "".join("{:02X}".format(b) for b in uid)


def _hex_fmt(hex_str):
    """'AABBCCDD' → 'AA BB CC DD'"""
    return " ".join(hex_str[i:i+2] for i in range(0, len(hex_str), 2))


# ── Persistence ───────────────────────────────────────────────────────────────

_RFID_DIR = "/flash/rfid"


def _ensure_dir():
    try:
        os.stat(_RFID_DIR)
    except OSError:
        os.mkdir(_RFID_DIR)


def _save_tag(uid, scan_count):
    _ensure_dir()
    h = _uid_hex(uid)
    path = "{}/{}.json".format(_RFID_DIR, h)
    try:
        with open(path, "w") as f:
            f.write('{{"uid":"{}","type":"ISO-14443A","scans":{}}}'.format(
                h, scan_count))
        return True
    except Exception:
        return False


def _list_saved():
    try:
        return sorted(f for f in os.listdir(_RFID_DIR) if f.endswith(".json"))
    except OSError:
        return []


def _load_tag(fname):
    try:
        with open("{}/{}".format(_RFID_DIR, fname)) as f:
            raw = f.read()
        d = {}
        for part in raw.strip("{}").split(","):
            if ":" not in part:
                continue
            k, v = part.split(":", 1)
            d[k.strip().strip('"')] = v.strip().strip('"')
        return d
    except Exception:
        return {}


# ── Display ───────────────────────────────────────────────────────────────────

def _font():
    try:
        _LCD.setFont(_LCD.FONTS.DejaVu9)
    except Exception:
        pass


def _header(title):
    _LCD.fillRect(0, 0, _W, 16, _DARK)
    _LCD.fillRect(0, 16, _W, 1, _ORANGE)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _DARK)
    _LCD.drawString(title, 6, 3)


def _hint(text):
    _LCD.fillRect(0, _H - 16, _W, 16, _DARK)
    _LCD.setTextSize(1)
    _LCD.setTextColor(_GRAY, _DARK)
    x = max((_W - _LCD.textWidth(text)) // 2, 4)
    _LCD.drawString(text, x, _H - 12)


def _center(text, y, color):
    _LCD.setTextSize(1)
    _LCD.setTextColor(color, _BLACK)
    _LCD.drawString(text, max((_W - _LCD.textWidth(text)) // 2, 4), y)


def _draw_scan(msg="Waiting for tag...", color=_GRAY):
    _LCD.fillScreen(_BLACK)
    _header("RFID Reader")
    _center(msg, 42, color)
    _center("Hold card near RFID unit", 62, 0x444444)
    _hint("L list   Q exit")


def _draw_found(uid, saved=False):
    _LCD.fillScreen(_BLACK)
    _header("RFID Reader")
    _LCD.setTextSize(1)
    _LCD.setTextColor(_ORANGE, _BLACK)
    _LCD.drawString("Tag detected!", 6, 24)
    _LCD.setTextColor(_CREAM, _BLACK)
    _LCD.drawString("UID:", 6, 40)
    _LCD.setTextColor(_GREEN, _BLACK)
    _LCD.drawString(_uid_str(uid), 6, 54)
    _LCD.setTextColor(_GRAY, _BLACK)
    _LCD.drawString("ISO-14443A", 6, 70)
    if saved:
        _LCD.setTextColor(_TEAL, _BLACK)
        _LCD.drawString("Saved.", 6, 86)
    _hint("S save   L list   ESC scan")


def _draw_list(files, cursor, scroll):
    _LCD.fillScreen(_BLACK)
    _header("Saved Tags")
    if not files:
        _center("No saved tags", 55, _GRAY)
        _hint("Q back")
        return
    y, row_h, max_vis = 22, 16, 5
    for i, fname in enumerate(files[scroll:scroll + max_vis]):
        abs_i = scroll + i
        label = _hex_fmt(fname[:-5])  # strip .json
        if abs_i == cursor:
            _LCD.fillRect(4, y - 2, _W - 8, row_h - 2, _ORANGE)
            _LCD.setTextColor(_BLACK, _ORANGE)
        else:
            _LCD.setTextColor(_CREAM, _BLACK)
        _LCD.drawString(label, 8, y)
        y += row_h
    if scroll > 0:
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("^", _W - 14, 22)
    if scroll + max_vis < len(files):
        _LCD.setTextColor(_ORANGE, _BLACK)
        _LCD.drawString("v", _W - 14, 22 + (min(len(files) - scroll, max_vis) - 1) * row_h)
    _hint("; . nav   Enter view   Q back")


def _draw_view(tag):
    _LCD.fillScreen(_BLACK)
    _header("Tag Detail")
    _LCD.setTextSize(1)
    uid = tag.get("uid", "?")
    _LCD.setTextColor(_ORANGE, _BLACK)
    _LCD.drawString("UID:", 6, 24)
    _LCD.setTextColor(_GREEN, _BLACK)
    _LCD.drawString(_hex_fmt(uid), 6, 38)
    _LCD.setTextColor(_GRAY, _BLACK)
    _LCD.drawString("Type:  " + tag.get("type", "?"), 6, 60)
    _LCD.drawString("Scans: " + tag.get("scans", "1"), 6, 76)
    _hint("Q back")


def _draw_error(msg):
    _LCD.fillScreen(_BLACK)
    _header("RFID Reader")
    _center("RFID unit not found", 38, _RED)
    _center(msg, 56, _GRAY)
    _center("Check Port A connection", 72, 0x444444)
    _hint("Q exit")


# ── Input ─────────────────────────────────────────────────────────────────────

def _intent(k):
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D): return "enter"
        if k == 0x1B:         return "back"
        if 0x20 <= k <= 0x7E: k = chr(k)
        else: return None
    if not isinstance(k, str) or not k:
        return None
    ch = k.lower()
    if ch in (";", ",", "w"): return "up"
    if ch in (".", "/"):      return "down"
    if ch in ("\r", "\n"):    return "enter"
    if ch in ("q", "\x1b"):   return "back"
    if ch == "l":             return "list"
    if ch == "s":             return "save"
    return None


# ── States ────────────────────────────────────────────────────────────────────
_SCAN  = 0
_FOUND = 1
_LIST  = 2
_VIEW  = 3


# ── Run ───────────────────────────────────────────────────────────────────────

def run():
    _font()
    kb = MatrixKeyboard()
    time.sleep_ms(300)

    rfid = None
    try:
        i2c  = I2C(1, sda=Pin(2), scl=Pin(1), freq=400000)
        rfid = _RFID(i2c)
        v    = rfid.version()
        if v not in (0x91, 0x92, 0xB2):
            # Some genuine MFRC522 chips report 0x91/0x92;
            # WS1850S variants may differ — still try.
            pass
    except Exception as e:
        _draw_error(str(e)[:30])
        while True:
            kb.tick()
            if _intent(kb.get_key()) == "back":
                return
            time.sleep_ms(40)

    state      = _SCAN
    last_uid   = None
    saved_flag = False
    scan_counts = {}

    list_files  = []
    list_cursor = 0
    list_scroll = 0
    view_tag    = {}

    poll_t = time.ticks_ms()
    _draw_scan()

    while True:
        kb.tick()
        intent = _intent(kb.get_key())

        # ── navigation ────────────────────────────────────────────────────
        if intent == "back":
            if state == _VIEW:
                state = _LIST
                _draw_list(list_files, list_cursor, list_scroll)
            elif state == _LIST:
                state = _SCAN
                last_uid = None
                _draw_scan()
            elif state == _FOUND:
                state = _SCAN
                last_uid = None
                saved_flag = False
                _draw_scan()
            else:
                return  # exit app

        elif intent == "list" and state in (_SCAN, _FOUND):
            list_files  = _list_saved()
            list_cursor = 0
            list_scroll = 0
            state       = _LIST
            _draw_list(list_files, list_cursor, list_scroll)

        elif intent == "save" and state == _FOUND and last_uid and not saved_flag:
            h  = _uid_hex(last_uid)
            sc = scan_counts.get(h, 1)
            _save_tag(last_uid, sc)
            saved_flag = True
            _draw_found(last_uid, saved=True)

        elif state == _LIST:
            if not list_files:
                pass
            elif intent == "up":
                list_cursor = (list_cursor - 1) % len(list_files)
                if list_cursor < list_scroll:
                    list_scroll = list_cursor
                elif list_cursor > list_scroll + 4:
                    list_scroll = max(0, len(list_files) - 5)
                _draw_list(list_files, list_cursor, list_scroll)
            elif intent == "down":
                list_cursor = (list_cursor + 1) % len(list_files)
                if list_cursor >= list_scroll + 5:
                    list_scroll = list_cursor - 4
                elif list_cursor < list_scroll:
                    list_scroll = 0
                _draw_list(list_files, list_cursor, list_scroll)
            elif intent == "enter":
                view_tag = _load_tag(list_files[list_cursor])
                state    = _VIEW
                _draw_view(view_tag)

        # ── RFID polling (scan state only, 200 ms interval) ───────────────
        if state == _SCAN:
            now = time.ticks_ms()
            if time.ticks_diff(now, poll_t) >= 200:
                poll_t = now
                try:
                    if rfid.request():
                        ok, uid = rfid.anticoll()
                        if ok:
                            h = _uid_hex(uid)
                            scan_counts[h] = scan_counts.get(h, 0) + 1
                            last_uid   = uid
                            saved_flag = False
                            state      = _FOUND
                            _draw_found(uid)
                        rfid.halt()
                except Exception:
                    pass

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
