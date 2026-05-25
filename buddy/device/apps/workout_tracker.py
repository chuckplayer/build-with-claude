"""Workout Tracker for Cardputer-Adv (M5Stack / MicroPython / UIFlow 2.0).

Tracks push/pull/legs/full-body splits.  Records sets, checks PRs via the
Brzycki 1-RM estimate, and persists history + PRs to flash.

Keys (context-sensitive — see hint strip):
  ; , w     scroll / cursor up
  . / s     scroll / cursor down
  Space     toggle selection (picker)
  Enter     confirm / log set
  j k       weight down / up  (active screen)
  u d       reps down / up    (active screen)
  n         next exercise     (active screen)
  f         finish workout    (active / rest screen)
  D         delete entry      (home screen)
  q ESC     back / exit
"""

try:
    import ujson as _json
except ImportError:
    import json as _json

try:
    import utime as _time
except ImportError:
    import time as _time

# ── Palette ──────────────────────────────────────────────────────────────────
_BLACK  = 0x000000
_ORANGE = 0xCC785C
_CREAM  = 0xF0EEE6
_DARK   = 0x1F1F1F
_GRAY   = 0x777777
_GREEN  = 0x00FF00
_RED    = 0xFF0000
_TEAL   = 0x00BFBF

# ── Layout ───────────────────────────────────────────────────────────────────
_W, _H = 240, 135

# ── Persistence ──────────────────────────────────────────────────────────────
_HISTORY_FILE = "/flash/workout_history.json"
_PRS_FILE     = "/flash/workout_prs.json"

# ── Workout config ────────────────────────────────────────────────────────────
_REST_SECS   = 90
_WEIGHT_STEP = 5
_MAX_HISTORY = 20

# ── Exercise library ──────────────────────────────────────────────────────────
_SPLIT_NAMES = ("Push", "Pull", "Legs", "Full Body")
_SPLIT_EXERCISES = {
    "Push":      ["Bench Press", "Overhead Press", "Incline Bench", "Cable Fly",
                  "Tricep Pushdown", "Dip", "Lateral Raise", "Chest Dip"],
    "Pull":      ["Deadlift", "Barbell Row", "Lat Pulldown", "Cable Row",
                  "Pull-Up", "Dumbbell Curl", "Face Pull", "Hammer Curl"],
    "Legs":      ["Squat", "Romanian Deadlift", "Leg Press", "Leg Curl",
                  "Leg Extension", "Calf Raise", "Bulgarian Split Squat", "Hack Squat"],
    "Full Body": ["Squat", "Bench Press", "Deadlift", "Overhead Press",
                  "Barbell Row", "Pull-Up", "Dip"],
}

# ── Mode constants ────────────────────────────────────────────────────────────
_MODE_HOME    = 0
_MODE_SPLIT   = 1
_MODE_PICKER  = 2
_MODE_ACTIVE  = 3
_MODE_REST    = 4
_MODE_SUMMARY = 5
_MODE_DELETE  = 6
_MODE_ADD_EX  = 7

# ── Accelerometer rep counter constants ───────────────────────────────────────
_RC_IDLE   = 0
_RC_PEAK1  = 1   # first movement (e.g. push/pull)
_RC_VALLEY = 2   # between movements (at apex/lockout)
_RC_PEAK2  = 3   # return movement
_RC_LOW_SQ   = 1.1
_RC_HIGH_SQ  = 1.8
_RC_DEBOUNCE = 600
_FLASH_MS    = 200


# ── Key normalisation ─────────────────────────────────────────────────────────

def _normalize_key(k):
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D):
            return "\n"
        if k == 0x1B:
            return "\x1b"
        if 0x20 <= k <= 0x7E:
            return chr(k).lower()
        return None
    if isinstance(k, str) and k:
        return k.lower()
    return None


# ── Math / date helpers ───────────────────────────────────────────────────────

def _e1rm(weight, reps):
    if reps <= 1:
        return weight
    return int(weight * (1 + reps / 30))


def _today_str():
    try:
        t = _time.localtime()
        return "{}-{:02d}-{:02d}".format(t[0], t[1], t[2])
    except Exception:
        return "unknown"


# ── JSON persistence ──────────────────────────────────────────────────────────

def _load_json(path, default):
    try:
        with open(path) as f:
            return _json.load(f)
    except Exception:
        return default


def _save_json(path, data):
    try:
        with open(path, "w") as f:
            _json.dump(data, f)
    except Exception:
        pass


def _load_history():
    return _load_json(_HISTORY_FILE, [])


def _save_history(h):
    _save_json(_HISTORY_FILE, h)


def _load_prs():
    return _load_json(_PRS_FILE, {})


def _save_prs(p):
    _save_json(_PRS_FILE, p)


def _check_and_save_pr(prs, exercise, weight, reps):
    e1 = _e1rm(weight, reps)
    existing = prs.get(exercise, {})
    if e1 > existing.get("e1rm", 0):
        prs[exercise] = {"weight": weight, "reps": reps, "e1rm": e1}
        _save_prs(prs)
        return True
    return False


def _do_finish_and_save(session, split_name, history):
    entry = {
        "date": _today_str(),
        "split": split_name,
        "exercises": session,
    }
    history.append(entry)
    if len(history) > _MAX_HISTORY:
        del history[0:len(history) - _MAX_HISTORY]
    _save_history(history)


# ── Accelerometer rep counter ─────────────────────────────────────────────────

def _rc_new():
    return {"state": _RC_IDLE, "last_ms": 0, "count": 0}


def _rc_sample(rc, xyz, now_ms):
    x, y, z = xyz
    mag_sq = x * x + y * y + z * z
    just_inc = False
    state = rc["state"]
    if state == _RC_IDLE:
        if mag_sq > _RC_HIGH_SQ and (now_ms - rc["last_ms"]) >= _RC_DEBOUNCE:
            rc["state"] = _RC_PEAK1
    elif state == _RC_PEAK1:
        if mag_sq < _RC_LOW_SQ:
            rc["state"] = _RC_VALLEY
    elif state == _RC_VALLEY:
        if mag_sq > _RC_HIGH_SQ:
            rc["state"] = _RC_PEAK2
    elif state == _RC_PEAK2:
        if mag_sq < _RC_LOW_SQ:
            rc["count"] += 1
            rc["last_ms"] = now_ms
            rc["state"] = _RC_IDLE
            just_inc = True
    return rc["count"], just_inc


# ── Speaker beeps ─────────────────────────────────────────────────────────────

def _beep_pr():
    try:
        import M5
        M5.Speaker.tone(880, 100)
        _time.sleep_ms(150)
        M5.Speaker.tone(1046, 150)
    except Exception:
        pass


def _beep_rest_done():
    try:
        import M5
        M5.Speaker.tone(523, 200)
    except Exception:
        pass


# ── Chrome helpers ────────────────────────────────────────────────────────────

def _draw_header(Lcd, title, badge=None):
    Lcd.fillRect(0, 0, _W, 17, _DARK)
    Lcd.setTextColor(_CREAM, _DARK)
    Lcd.setTextSize(1)
    Lcd.drawString(title, 4, 3)
    if badge is not None:
        bw = Lcd.textWidth(str(badge))
        Lcd.setTextColor(_ORANGE, _DARK)
        Lcd.drawString(str(badge), _W - bw - 4, 3)
    Lcd.drawLine(0, 17, _W, 17, _ORANGE)


def _draw_hint(Lcd, text):
    Lcd.fillRect(0, _H - 18, _W, 18, _DARK)
    Lcd.setTextColor(_GRAY, _DARK)
    Lcd.setTextSize(1)
    Lcd.drawString(text, 4, _H - 14)


def _clear_body(Lcd):
    Lcd.fillRect(0, 18, _W, 99, _BLACK)


# ── HOME screen ───────────────────────────────────────────────────────────────

def _draw_home(Lcd, history, cursor, scroll_top):
    _draw_header(Lcd, "Workout Tracker")
    _draw_hint(Lcd, "Enter start  D delete  Q exit")
    _clear_body(Lcd)

    # Row 0: pinned "+ New Workout"
    if cursor == 0:
        Lcd.fillRect(4, 19, _W - 8, 16, _ORANGE)
        Lcd.setTextColor(_BLACK, _ORANGE)
    else:
        Lcd.fillRect(4, 19, _W - 8, 16, _DARK)
        Lcd.setTextColor(_ORANGE, _DARK)
    Lcd.drawString("+ New Workout", 8, 21)

    # Rows 1..5: history in reverse chronological order
    hist_len = len(history)
    for i in range(5):
        hist_idx = hist_len - 1 - (scroll_top + i)
        if hist_idx < 0:
            break
        entry = history[hist_idx]
        date_str = entry.get("date", "?")
        split_str = entry.get("split", "")
        exercises = entry.get("exercises", [])
        set_count = 0
        for ex in exercises:
            set_count += len(ex.get("sets", []))
        label = "{} \xb7 {} \xb7 {} sets".format(date_str, split_str, set_count)
        y = 37 + i * 16
        display_pos = scroll_top + i   # 0-based position in the reversed display
        if cursor > 0 and display_pos == cursor - 1:
            Lcd.fillRect(4, y - 1, _W - 8, 16, _ORANGE)
            Lcd.setTextColor(_BLACK, _ORANGE)
        else:
            Lcd.fillRect(4, y - 1, _W - 8, 16, _BLACK)
            Lcd.setTextColor(_CREAM, _BLACK)
        Lcd.drawString(label, 8, y)

    # Scroll indicators
    Lcd.setTextColor(_ORANGE, _BLACK)
    if scroll_top > 0:
        Lcd.drawString("^", 228, 37)
    if scroll_top + 5 < hist_len:
        Lcd.drawString("v", 228, 37 + 4 * 16)


# ── DELETE MODAL ──────────────────────────────────────────────────────────────

def _draw_delete_modal(Lcd, label):
    # Filled background
    Lcd.fillRect(20, 43, 200, 50, _DARK)
    # Orange border (4 lines)
    Lcd.drawLine(20, 43, 219, 43, _ORANGE)
    Lcd.drawLine(20, 92, 219, 92, _ORANGE)
    Lcd.drawLine(20, 43, 20, 92, _ORANGE)
    Lcd.drawLine(219, 43, 219, 92, _ORANGE)

    # "Delete workout?" centered in CREAM at y=50
    title = "Delete workout?"
    tw = Lcd.textWidth(title)
    Lcd.setTextColor(_CREAM, _DARK)
    Lcd.setTextSize(1)
    Lcd.drawString(title, ((_W - tw) // 2), 50)

    # Truncated label in GRAY at y=62
    short_label = label[:28] if len(label) > 28 else label
    lw = Lcd.textWidth(short_label)
    Lcd.setTextColor(_GRAY, _DARK)
    Lcd.drawString(short_label, ((_W - lw) // 2), 62)

    # Confirm/cancel hint in ORANGE at y=77
    conf = "Y confirm   N cancel"
    cw = Lcd.textWidth(conf)
    Lcd.setTextColor(_ORANGE, _DARK)
    Lcd.drawString(conf, ((_W - cw) // 2), 77)


# ── SPLIT screen ──────────────────────────────────────────────────────────────

def _draw_split(Lcd, cursor):
    _draw_header(Lcd, "Select Split")
    _draw_hint(Lcd, "; . scroll  Enter select  Q back")
    _clear_body(Lcd)
    for i in range(len(_SPLIT_NAMES)):
        name = _SPLIT_NAMES[i]
        y = 22 + i * 22
        x = (_W - Lcd.textWidth(name)) // 2
        if i == cursor:
            Lcd.fillRect(4, y - 2, _W - 8, 18, _ORANGE)
            Lcd.setTextColor(_BLACK, _ORANGE)
        else:
            Lcd.fillRect(4, y - 2, _W - 8, 18, _BLACK)
            Lcd.setTextColor(_CREAM, _BLACK)
        Lcd.drawString(name, x, y)


# ── PICKER screen ─────────────────────────────────────────────────────────────

def _draw_picker(Lcd, split_name, exercises, selected_set, cursor, scroll_top):
    badge = "{} sel".format(len(selected_set))
    _draw_header(Lcd, split_name + " Day", badge)
    _draw_hint(Lcd, "Space toggle  Enter start  Q back")
    _clear_body(Lcd)
    row_h = 16
    num_visible = 6
    for i in range(num_visible):
        idx = scroll_top + i
        if idx >= len(exercises):
            break
        name = exercises[idx]
        if name in selected_set:
            prefix = "[v] "
        else:
            prefix = "[ ] "
        y = 20 + i * row_h
        if idx == cursor:
            Lcd.fillRect(4, y - 1, _W - 8, row_h, _ORANGE)
            Lcd.setTextColor(_BLACK, _ORANGE)
        else:
            Lcd.fillRect(4, y - 1, _W - 8, row_h, _BLACK)
            Lcd.setTextColor(_CREAM, _BLACK)
        Lcd.drawString(prefix + name, 8, y)

    # Scroll indicators
    Lcd.setTextColor(_ORANGE, _BLACK)
    if scroll_top > 0:
        Lcd.drawString("^", 228, 20)
    if scroll_top + num_visible < len(exercises):
        Lcd.drawString("v", 228, 20 + (num_visible - 1) * row_h)


# ── ACTIVE screen ─────────────────────────────────────────────────────────────

def _draw_active(Lcd, exercise, set_num, weight, reps, pr_e1rm, rep_color=None):
    badge = "S{}".format(set_num)
    _draw_header(Lcd, exercise[:18], badge)
    _draw_hint(Lcd, "j/k wt  u/d r  Ent log  P/N  A add  R rm  F")
    _clear_body(Lcd)

    # Large weight number
    Lcd.setTextSize(3)
    w_str = str(weight)
    wx = (_W - Lcd.textWidth(w_str)) // 2
    Lcd.setTextColor(_CREAM, _BLACK)
    Lcd.drawString(w_str, wx, 28)

    # "lbs" label
    Lcd.setTextSize(1)
    Lcd.setTextColor(_GRAY, _BLACK)
    lx = (_W - Lcd.textWidth("lbs")) // 2
    Lcd.drawString("lbs", lx, 58)

    # Large reps
    Lcd.setTextSize(2)
    r_color = rep_color if rep_color else _ORANGE
    r_str = str(reps)
    rx = (_W - Lcd.textWidth(r_str)) // 2
    Lcd.setTextColor(r_color, _BLACK)
    Lcd.drawString(r_str, rx, 70)
    Lcd.setTextSize(1)

    # "REPS" label
    Lcd.setTextColor(_GRAY, _BLACK)
    rpx = (_W - Lcd.textWidth("REPS")) // 2
    Lcd.drawString("REPS", rpx, 90)

    # 1RM display
    if pr_e1rm > 0:
        Lcd.setTextColor(_TEAL, _BLACK)
        Lcd.drawString("1RM: {}".format(pr_e1rm), 4, 100)


def _draw_rep_number(Lcd, reps, color):
    Lcd.fillRect(0, 65, _W, 28, _BLACK)
    Lcd.setTextSize(2)
    r_str = str(reps)
    rx = (_W - Lcd.textWidth(r_str)) // 2
    Lcd.setTextColor(color, _BLACK)
    Lcd.drawString(r_str, rx, 70)
    Lcd.setTextSize(1)


# ── REST screen ───────────────────────────────────────────────────────────────

def _draw_rest_static(Lcd, last_weight, last_reps, new_pr, next_ex):
    if next_ex:
        badge = next_ex[:12]
    else:
        badge = "Done"
    _draw_header(Lcd, "Rest", badge)
    _draw_hint(Lcd, "any key next set  F finish  Q exit")
    _clear_body(Lcd)

    set_str = "{}lbs x {}".format(last_weight, last_reps)
    sx = (_W - Lcd.textWidth(set_str)) // 2
    Lcd.setTextColor(_GRAY, _BLACK)
    Lcd.drawString(set_str, sx, 28)

    if new_pr:
        pr_str = "NEW PR!"
        px = (_W - Lcd.textWidth(pr_str)) // 2
        Lcd.setTextColor(_GREEN, _BLACK)
        Lcd.drawString(pr_str, px, 44)


def _draw_rest_timer(Lcd, secs_left):
    Lcd.fillRect(0, 56, _W, 50, _BLACK)
    mins = secs_left // 60
    secs = secs_left % 60
    t_str = "{}:{:02d}".format(mins, secs)
    Lcd.setTextSize(3)
    tx = (_W - Lcd.textWidth(t_str)) // 2
    Lcd.setTextColor(_ORANGE, _BLACK)
    Lcd.drawString(t_str, tx, 62)
    Lcd.setTextSize(1)


# ── ADD EXERCISE screen ───────────────────────────────────────────────────────

def _draw_add_ex(Lcd, exercises, cursor, scroll_top):
    _draw_header(Lcd, "Add Exercise")
    _draw_hint(Lcd, "; . scroll  Enter add  Q cancel")
    _clear_body(Lcd)
    row_h = 16
    visible = exercises[scroll_top:scroll_top + 6]
    for i, name in enumerate(visible):
        idx = scroll_top + i
        y = 20 + i * row_h
        if idx == cursor:
            Lcd.fillRect(4, y - 1, _W - 8, row_h, _ORANGE)
            Lcd.setTextColor(_BLACK, _ORANGE)
        else:
            Lcd.fillRect(4, y - 1, _W - 8, row_h, _BLACK)
            Lcd.setTextColor(_CREAM, _BLACK)
        Lcd.drawString(name, 8, y)
    Lcd.setTextColor(_ORANGE, _BLACK)
    if scroll_top > 0:
        Lcd.drawString("^", 228, 20)
    if scroll_top + 6 < len(exercises):
        Lcd.drawString("v", 228, 20 + 5 * row_h)


# ── SUMMARY screen ────────────────────────────────────────────────────────────

def _draw_summary(Lcd, session, split_name, prs):
    _draw_header(Lcd, "Workout Done!", split_name)
    _draw_hint(Lcd, "Enter home  Q exit")
    _clear_body(Lcd)

    total_sets = 0
    total_vol = 0
    for ex in session:
        total_sets += len(ex.get("sets", []))
        for s in ex.get("sets", []):
            total_vol += s["weight"] * s["reps"]

    summary_line = "Sets: {}  Vol: {}lbs".format(total_sets, total_vol)
    Lcd.setTextColor(_CREAM, _BLACK)
    Lcd.setTextSize(1)
    Lcd.drawString(summary_line, 8, 24)

    y = 40
    for ex in session:
        if y > 40 + 3 * 16:
            break
        sets = ex.get("sets", [])
        if not sets:
            continue
        last = sets[-1]
        ex_pr = prs.get(ex["name"], {})
        is_pr = bool(ex_pr) and _e1rm(last["weight"], last["reps"]) >= ex_pr.get("e1rm", 0)
        line = "{}  {}x{}".format(ex["name"][:14], last["weight"], last["reps"])
        if is_pr:
            Lcd.setTextColor(_GREEN, _BLACK)
        else:
            Lcd.setTextColor(_CREAM, _BLACK)
        Lcd.drawString(line, 8, y)
        y += 16


# ── Main state machine ────────────────────────────────────────────────────────

def run():
    import M5
    Lcd = M5.Lcd
    from hardware import MatrixKeyboard
    import machine

    # Try to get IMU reference
    try:
        from M5 import Imu
        _imu = Imu
    except Exception:
        _imu = None

    M5.begin()
    Lcd.setFont(Lcd.FONTS.DejaVu9)
    kb = MatrixKeyboard()

    history = _load_history()
    prs = _load_prs()

    mode = _MODE_HOME

    # HOME state
    home_cursor = 0
    home_scroll = 0
    delete_idx = None

    # SPLIT state
    split_cursor = 0

    # PICKER state
    split_name = ""
    picker_exercises = []
    picker_selected = set()
    picker_cursor = 0
    picker_scroll = 0

    # ACTIVE state
    active_exercises = []
    active_idx = 0
    set_num = 1
    cur_weight = 135
    cur_reps = 5
    rc = _rc_new()
    rep_flash_until = 0

    # ADD_EX state
    add_ex_list = []    # exercises available to add (split list minus already active)
    add_ex_cursor = 0
    add_ex_scroll = 0

    # Session accumulator
    session = []

    # REST state
    rest_end_ms = 0
    last_weight = 0
    last_reps = 0
    new_pr = False

    Lcd.fillScreen(_BLACK)
    _draw_home(Lcd, history, home_cursor, home_scroll)

    try:
        while True:
            M5.update()
            kb.tick()
            ch = _normalize_key(kb.get_key())
            now_ms = _time.ticks_ms()

            # ── HOME ─────────────────────────────────────────────────────────
            if mode == _MODE_HOME:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                max_cursor = len(history)
                if ch in (";", ",", "w"):
                    if home_cursor > 0:
                        home_cursor -= 1
                    else:
                        home_cursor = max_cursor
                    if home_cursor == 0:
                        home_scroll = 0
                    elif home_cursor - 1 < home_scroll:
                        home_scroll = home_cursor - 1
                    _draw_home(Lcd, history, home_cursor, home_scroll)
                elif ch in (".", "/", "s"):
                    if home_cursor < max_cursor:
                        home_cursor += 1
                    else:
                        home_cursor = 0
                    if home_cursor > 0 and home_cursor - 1 >= home_scroll + 5:
                        home_scroll = home_cursor - 5
                    _draw_home(Lcd, history, home_cursor, home_scroll)
                elif ch == "\n":
                    if home_cursor == 0:
                        split_cursor = 0
                        mode = _MODE_SPLIT
                        _draw_split(Lcd, split_cursor)
                    # history row taps are no-op in v1
                elif ch == "d" and home_cursor > 0:
                    actual_hist_idx = len(history) - 1 - (home_scroll + (home_cursor - 1))
                    if 0 <= actual_hist_idx < len(history):
                        entry = history[actual_hist_idx]
                        label = "{} \xb7 {}".format(
                            entry.get("date", "?"),
                            entry.get("split", "—"),
                        )
                        delete_idx = actual_hist_idx
                        mode = _MODE_DELETE
                        _draw_delete_modal(Lcd, label)
                elif ch in ("q", "\x1b"):
                    break

            # ── DELETE MODAL ──────────────────────────────────────────────────
            elif mode == _MODE_DELETE:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch == "y":
                    if delete_idx is not None and 0 <= delete_idx < len(history):
                        del history[delete_idx]
                        _save_history(history)
                    delete_idx = None
                    home_cursor = 0
                    home_scroll = 0
                    mode = _MODE_HOME
                    Lcd.fillScreen(_BLACK)
                    _draw_home(Lcd, history, home_cursor, home_scroll)
                elif ch in ("n", "q", "\x1b"):
                    delete_idx = None
                    mode = _MODE_HOME
                    Lcd.fillScreen(_BLACK)
                    _draw_home(Lcd, history, home_cursor, home_scroll)

            # ── SPLIT ─────────────────────────────────────────────────────────
            elif mode == _MODE_SPLIT:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch in (";", ",", "w"):
                    if split_cursor > 0:
                        split_cursor -= 1
                    else:
                        split_cursor = len(_SPLIT_NAMES) - 1
                    _draw_split(Lcd, split_cursor)
                elif ch in (".", "/", "s"):
                    if split_cursor < len(_SPLIT_NAMES) - 1:
                        split_cursor += 1
                    else:
                        split_cursor = 0
                    _draw_split(Lcd, split_cursor)
                elif ch == "\n":
                    split_name = _SPLIT_NAMES[split_cursor]
                    picker_exercises = list(_SPLIT_EXERCISES[split_name])
                    picker_selected = set()
                    picker_cursor = 0
                    picker_scroll = 0
                    mode = _MODE_PICKER
                    _draw_picker(Lcd, split_name, picker_exercises, picker_selected,
                                 picker_cursor, picker_scroll)
                elif ch in ("q", "\x1b"):
                    mode = _MODE_HOME
                    _draw_home(Lcd, history, home_cursor, home_scroll)

            # ── PICKER ────────────────────────────────────────────────────────
            elif mode == _MODE_PICKER:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch in (";", ",", "w"):
                    if picker_cursor > 0:
                        picker_cursor -= 1
                    else:
                        picker_cursor = len(picker_exercises) - 1
                    if picker_cursor < picker_scroll:
                        picker_scroll = picker_cursor
                    _draw_picker(Lcd, split_name, picker_exercises, picker_selected,
                                 picker_cursor, picker_scroll)
                elif ch in (".", "/", "s"):
                    if picker_cursor < len(picker_exercises) - 1:
                        picker_cursor += 1
                    else:
                        picker_cursor = 0
                        picker_scroll = 0
                    if picker_cursor >= picker_scroll + 6:
                        picker_scroll = picker_cursor - 5
                    _draw_picker(Lcd, split_name, picker_exercises, picker_selected,
                                 picker_cursor, picker_scroll)
                elif ch == " ":
                    name = picker_exercises[picker_cursor]
                    if name in picker_selected:
                        picker_selected.discard(name)
                    else:
                        picker_selected.add(name)
                    _draw_picker(Lcd, split_name, picker_exercises, picker_selected,
                                 picker_cursor, picker_scroll)
                elif ch == "\n":
                    if len(picker_selected) > 0:
                        active_exercises = [e for e in picker_exercises if e in picker_selected]
                        if not active_exercises:
                            continue
                        active_idx = 0
                        set_num = 1
                        cur_weight = 135
                        cur_reps = 5
                        rc = _rc_new()
                        rep_flash_until = 0
                        session = []
                        pr_e1rm = prs.get(active_exercises[0], {}).get("e1rm", 0)
                        mode = _MODE_ACTIVE
                        _draw_active(Lcd, active_exercises[0], set_num, cur_weight,
                                     cur_reps, pr_e1rm)
                elif ch in ("q", "\x1b"):
                    mode = _MODE_SPLIT
                    _draw_split(Lcd, split_cursor)

            # ── ACTIVE ────────────────────────────────────────────────────────
            elif mode == _MODE_ACTIVE:
                # Sample IMU for rep counting
                if _imu is not None:
                    try:
                        xyz = _imu.getAccel()
                        count, just_inc = _rc_sample(rc, xyz, now_ms)
                        if just_inc:
                            cur_reps = count
                            rep_flash_until = now_ms + _FLASH_MS
                            _draw_rep_number(Lcd, cur_reps, _GREEN)
                        elif rep_flash_until > 0 and now_ms >= rep_flash_until:
                            _draw_rep_number(Lcd, cur_reps, _ORANGE)
                            rep_flash_until = 0
                    except Exception:
                        pass

                if ch is None:
                    _time.sleep_ms(20)
                    continue

                ex_name = active_exercises[active_idx]
                pr_e1rm = prs.get(ex_name, {}).get("e1rm", 0)
                redraw = False

                if ch == "k":
                    cur_weight += _WEIGHT_STEP
                    redraw = True
                elif ch == "j":
                    cur_weight = max(0, cur_weight - _WEIGHT_STEP)
                    redraw = True
                elif ch == "u":
                    cur_reps += 1
                    rc["count"] = cur_reps
                    redraw = True
                elif ch == "d":
                    cur_reps = max(1, cur_reps - 1)
                    rc["count"] = cur_reps
                    redraw = True
                elif ch == "\n":
                    # Log the set
                    found = None
                    for ex in session:
                        if ex["name"] == ex_name:
                            found = ex
                            break
                    if found is None:
                        found = {"name": ex_name, "sets": []}
                        session.append(found)
                    found["sets"].append({"weight": cur_weight, "reps": cur_reps})
                    new_pr = _check_and_save_pr(prs, ex_name, cur_weight, cur_reps)
                    if new_pr:
                        _beep_pr()
                    last_weight = cur_weight
                    last_reps = cur_reps
                    set_num += 1
                    rc = _rc_new()
                    rep_flash_until = 0
                    rest_end_ms = _time.ticks_add(_time.ticks_ms(), _REST_SECS * 1000)
                    if active_idx + 1 < len(active_exercises):
                        next_ex = active_exercises[active_idx + 1]
                    else:
                        next_ex = None
                    mode = _MODE_REST
                    _draw_rest_static(Lcd, last_weight, last_reps, new_pr, next_ex)
                    _draw_rest_timer(Lcd, _REST_SECS)
                elif ch == "n":
                    if active_idx + 1 < len(active_exercises):
                        active_idx += 1
                        set_num = 1
                        cur_weight = 135
                        next_name = active_exercises[active_idx]
                        for ex in reversed(session):
                            if ex["name"] == next_name and ex.get("sets"):
                                cur_weight = ex["sets"][-1]["weight"]
                                break
                        cur_reps = 5
                        rc = _rc_new()
                        rep_flash_until = 0
                        pr_e1rm = prs.get(next_name, {}).get("e1rm", 0)
                        _draw_active(Lcd, next_name, set_num, cur_weight, cur_reps, pr_e1rm)
                    else:
                        _do_finish_and_save(session, split_name, history)
                        _draw_summary(Lcd, session, split_name, prs)
                        mode = _MODE_SUMMARY
                elif ch == "p":
                    if active_idx > 0:
                        active_idx -= 1
                        ex_name = active_exercises[active_idx]
                        set_num = 1
                        cur_weight = 135
                        for ex in reversed(session):
                            if ex["name"] == ex_name and ex["sets"]:
                                cur_weight = ex["sets"][-1]["weight"]
                                break
                        cur_reps = 5
                        rc = _rc_new()
                        rep_flash_until = 0
                        pr_e1rm = prs.get(ex_name, {}).get("e1rm", 0)
                        _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm)
                elif ch == "r":
                    # Remove current exercise from the queue (keep its sets in session for history)
                    if len(active_exercises) > 1:
                        del active_exercises[active_idx]
                        if active_idx >= len(active_exercises):
                            active_idx = len(active_exercises) - 1
                        ex_name = active_exercises[active_idx]
                        set_num = 1
                        cur_weight = 135
                        for ex in reversed(session):
                            if ex["name"] == ex_name and ex["sets"]:
                                cur_weight = ex["sets"][-1]["weight"]
                                break
                        cur_reps = 5
                        rc = _rc_new()
                        rep_flash_until = 0
                        pr_e1rm = prs.get(ex_name, {}).get("e1rm", 0)
                        _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm)
                    else:
                        # Last exercise — finish the workout
                        _do_finish_and_save(session, split_name, history)
                        _draw_summary(Lcd, session, split_name, prs)
                        mode = _MODE_SUMMARY
                elif ch == "a":
                    # Build list of exercises in this split not already in active_exercises
                    active_set = set(active_exercises)
                    add_ex_list = [e for e in _SPLIT_EXERCISES.get(split_name, []) if e not in active_set]
                    if add_ex_list:
                        add_ex_cursor = 0
                        add_ex_scroll = 0
                        mode = _MODE_ADD_EX
                        _draw_add_ex(Lcd, add_ex_list, add_ex_cursor, add_ex_scroll)
                    # If no exercises left to add, no-op
                elif ch == "f":
                    _do_finish_and_save(session, split_name, history)
                    _draw_summary(Lcd, session, split_name, prs)
                    mode = _MODE_SUMMARY
                elif ch in ("q", "\x1b"):
                    mode = _MODE_PICKER
                    _draw_picker(Lcd, split_name, picker_exercises, picker_selected,
                                 picker_cursor, picker_scroll)

                if redraw:
                    _draw_active(Lcd, active_exercises[active_idx], set_num,
                                 cur_weight, cur_reps, pr_e1rm)

            # ── REST ──────────────────────────────────────────────────────────
            elif mode == _MODE_REST:
                remaining_ms = _time.ticks_diff(rest_end_ms, _time.ticks_ms())
                secs_left = max(0, remaining_ms // 1000)
                if ch is not None:
                    if ch == "f":
                        _do_finish_and_save(session, split_name, history)
                        _draw_summary(Lcd, session, split_name, prs)
                        mode = _MODE_SUMMARY
                    elif ch in ("q", "\x1b"):
                        mode = _MODE_HOME
                        home_cursor = 0
                        home_scroll = 0
                        _draw_home(Lcd, history, home_cursor, home_scroll)
                    else:
                        # Any other key skips rest — return to active screen
                        ex_name = active_exercises[active_idx]
                        pr_e1rm = prs.get(ex_name, {}).get("e1rm", 0)
                        rc = _rc_new()
                        rep_flash_until = 0
                        mode = _MODE_ACTIVE
                        _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm)
                elif remaining_ms <= 0:
                    _beep_rest_done()
                    ex_name = active_exercises[active_idx]
                    pr_e1rm = prs.get(ex_name, {}).get("e1rm", 0)
                    rc = _rc_new()
                    rep_flash_until = 0
                    mode = _MODE_ACTIVE
                    _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm)
                else:
                    _draw_rest_timer(Lcd, secs_left)
                    _time.sleep_ms(500)
                continue

            # ── SUMMARY ───────────────────────────────────────────────────────
            elif mode == _MODE_SUMMARY:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch == "\n":
                    home_cursor = 0
                    home_scroll = 0
                    mode = _MODE_HOME
                    _draw_home(Lcd, history, home_cursor, home_scroll)
                elif ch in ("q", "\x1b"):
                    break

            # ── ADD_EX ────────────────────────────────────────────────────────
            elif mode == _MODE_ADD_EX:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch in (";", ",", "w"):
                    if add_ex_cursor > 0:
                        add_ex_cursor -= 1
                    else:
                        add_ex_cursor = len(add_ex_list) - 1
                    if add_ex_cursor < add_ex_scroll:
                        add_ex_scroll = add_ex_cursor
                    _draw_add_ex(Lcd, add_ex_list, add_ex_cursor, add_ex_scroll)
                elif ch in (".", "/", "s"):
                    if add_ex_cursor < len(add_ex_list) - 1:
                        add_ex_cursor += 1
                    else:
                        add_ex_cursor = 0
                        add_ex_scroll = 0
                    if add_ex_cursor >= add_ex_scroll + 6:
                        add_ex_scroll = add_ex_cursor - 5
                    _draw_add_ex(Lcd, add_ex_list, add_ex_cursor, add_ex_scroll)
                elif ch == "\n":
                    # Insert the selected exercise immediately after the current position
                    new_ex = add_ex_list[add_ex_cursor]
                    insert_pos = active_idx + 1
                    active_exercises.insert(insert_pos, new_ex)
                    mode = _MODE_ACTIVE
                    ex_name = active_exercises[active_idx]
                    pr_e1rm = prs.get(ex_name, {}).get("e1rm", 0)
                    _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm)
                elif ch in ("q", "\x1b"):
                    mode = _MODE_ACTIVE
                    ex_name = active_exercises[active_idx]
                    pr_e1rm = prs.get(ex_name, {}).get("e1rm", 0)
                    _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm)

            _time.sleep_ms(40)

    finally:
        machine.reset()


run()
