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
_HISTORY_FILE  = "/flash/workout_history.json"
_PRS_FILE      = "/flash/workout_prs.json"
_ROUTINES_FILE = "/flash/routines.json"

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
_MODE_HOME         = 0
_MODE_SPLIT        = 1
_MODE_PICKER       = 2
_MODE_ACTIVE       = 3
_MODE_REST         = 4
_MODE_SUMMARY      = 5
_MODE_DELETE       = 6
_MODE_ADD_EX       = 7
_MODE_ROUTINE_NAME = 8   # type a routine name
_MODE_ROUTINE_PICK = 9   # pick exercises for the routine
_MODE_CUSTOM_EX    = 10  # type a custom exercise name
_MODE_ROUTINE_DEL  = 11  # delete confirmation for a routine

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


def _raw_char(k):
    """Key normalizer for text entry: preserves case, handles backspace, no lowercase."""
    if k is None:
        return None
    if isinstance(k, int):
        if k in (0x0A, 0x0D):
            return "\n"
        if k == 0x1B:
            return "\x1b"
        if k in (0x08, 0x7F):
            return "\x08"   # backspace
        if 0x20 <= k <= 0x7E:
            return chr(k)   # preserve case — no .lower()
        return None
    if isinstance(k, str) and k:
        if k in ("\n", "\r"):
            return "\n"
        if k == "\x1b":
            return "\x1b"
        if k in ("\x08", "\x7f"):
            return "\x08"
        if len(k) == 1 and 0x20 <= ord(k[0]) <= 0x7E:
            return k        # preserve case
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


def _load_routines():
    return _load_json(_ROUTINES_FILE, [])


def _save_routines(r):
    _save_json(_ROUTINES_FILE, r)


def _all_exercises():
    """Return all exercises from all splits, deduplicated, sorted A-Z."""
    seen = set()
    result = []
    for split in _SPLIT_NAMES:
        for ex in _SPLIT_EXERCISES[split]:
            if ex not in seen:
                seen.add(ex)
                result.append(ex)
    result.sort()
    return result


def _pr_key(exercise, side=None):
    """Return the PR dict key for an exercise, with optional side suffix."""
    if side is not None:
        return "{} {}".format(exercise, side)
    return exercise


def _get_pr_e1rm(prs, exercise, side=None):
    """Return the stored e1RM for an exercise (0 if none)."""
    return prs.get(_pr_key(exercise, side), {}).get("e1rm", 0)


def _check_and_save_pr(prs, exercise, weight, reps, side=None):
    key = _pr_key(exercise, side)
    e1 = _e1rm(weight, reps)
    existing = prs.get(key, {})
    if e1 > existing.get("e1rm", 0):
        prs[key] = {"weight": weight, "reps": reps, "e1rm": e1}
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

def _draw_split(Lcd, routines, cursor, scroll_top):
    """
    Scrollable split/routine list.
    Items: routines[0..N-1] (teal) then _SPLIT_NAMES[0..3] (cream).
    cursor is an index into the combined list.
    scroll_top is the index of the top visible item.
    Shows 6 rows at 16px each starting at y=20.
    """
    num_routines = len(routines)
    total = num_routines + len(_SPLIT_NAMES)
    badge = None
    if num_routines > 0:
        badge = "{} saved".format(num_routines)
    _draw_header(Lcd, "Select Workout", badge)
    if cursor < num_routines:
        hint = "Enter start  C new  E edit  D del  Q back"
    else:
        hint = "Enter start  C new  Q back"
    _draw_hint(Lcd, hint)
    _clear_body(Lcd)

    row_h = 16
    num_visible = 6

    for i in range(num_visible):
        idx = scroll_top + i
        if idx >= total:
            break
        y = 20 + i * row_h

        if idx < num_routines:
            # Routine entry
            item = routines[idx]
            name = item.get("name", "Routine")
            ex_count = len(item.get("exercises", []))
            label = "{} ({})".format(name[:16], ex_count)
            if idx == cursor:
                Lcd.fillRect(4, y - 1, _W - 8, row_h, _TEAL)
                Lcd.setTextColor(_BLACK, _TEAL)
            else:
                Lcd.fillRect(4, y - 1, _W - 8, row_h, _BLACK)
                Lcd.setTextColor(_TEAL, _BLACK)
        else:
            # Built-in split entry
            split_idx = idx - num_routines
            label = _SPLIT_NAMES[split_idx]
            if idx == cursor:
                Lcd.fillRect(4, y - 1, _W - 8, row_h, _ORANGE)
                Lcd.setTextColor(_BLACK, _ORANGE)
            else:
                Lcd.fillRect(4, y - 1, _W - 8, row_h, _BLACK)
                Lcd.setTextColor(_CREAM, _BLACK)

        # Draw separator line between last routine and first split
        if num_routines > 0 and idx == num_routines:
            Lcd.drawLine(8, y - 2, _W - 8, y - 2, _GRAY)

        Lcd.drawString(label, 8, y)

    # Scroll indicators
    Lcd.setTextColor(_ORANGE, _BLACK)
    if scroll_top > 0:
        Lcd.drawString("^", 228, 20)
    if scroll_top + num_visible < total:
        Lcd.drawString("v", 228, 20 + (num_visible - 1) * row_h)


# ── TEXT ENTRY screen ────────────────────────────────────────────────────────

def _draw_text_entry(Lcd, title, buf, hint, max_len=18):
    _draw_header(Lcd, title)
    _draw_hint(Lcd, hint)
    _clear_body(Lcd)
    # Show typed text with cursor in center of body
    text = "".join(buf) + "|"
    # Truncate display to fit
    if len(text) > 26:
        text = text[-26:]
    Lcd.setTextColor(_CREAM, _BLACK)
    tx = (_W - Lcd.textWidth(text)) // 2
    Lcd.drawString(text, tx, 55)
    # Show char count
    count_str = "{}/{}".format(len(buf), max_len)
    Lcd.setTextColor(_GRAY, _BLACK)
    cx = (_W - Lcd.textWidth(count_str)) // 2
    Lcd.drawString(count_str, cx, 75)


# ── ROUTINE EXERCISE PICKER screen ───────────────────────────────────────────

def _draw_routine_pick(Lcd, exercises, selected_set, cursor, scroll_top):
    """
    exercises: full list including "+" sentinel at the end.
    "+" is the "Add custom..." entry.
    selected_set: set of selected exercise names.
    """
    badge = "{} sel".format(len(selected_set))
    _draw_header(Lcd, "Add Exercises", badge)
    _draw_hint(Lcd, "Space toggle  Enter done  Q back")
    _clear_body(Lcd)
    row_h = 16
    num_visible = 6
    for i in range(num_visible):
        idx = scroll_top + i
        if idx >= len(exercises):
            break
        name = exercises[idx]
        y = 20 + i * row_h
        if name == "+":
            # "Add custom..." sentinel
            display = "+ Add custom..."
            if idx == cursor:
                Lcd.fillRect(4, y - 1, _W - 8, row_h, _GRAY)
                Lcd.setTextColor(_BLACK, _GRAY)
            else:
                Lcd.fillRect(4, y - 1, _W - 8, row_h, _BLACK)
                Lcd.setTextColor(_GRAY, _BLACK)
            Lcd.drawString(display, 8, y)
        else:
            if name in selected_set:
                prefix = "[v] "
            else:
                prefix = "[ ] "
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


# ── ROUTINE DELETE CONFIRMATION modal ─────────────────────────────────────────

def _draw_routine_del_modal(Lcd, routine_name):
    Lcd.fillRect(20, 43, 200, 50, _DARK)
    Lcd.drawLine(20, 43, 219, 43, _ORANGE)
    Lcd.drawLine(20, 92, 219, 92, _ORANGE)
    Lcd.drawLine(20, 43, 20, 92, _ORANGE)
    Lcd.drawLine(219, 43, 219, 92, _ORANGE)
    title = "Delete routine?"
    tw = Lcd.textWidth(title)
    Lcd.setTextColor(_CREAM, _DARK)
    Lcd.setTextSize(1)
    Lcd.drawString(title, (_W - tw) // 2, 50)
    short = routine_name[:22] if len(routine_name) > 22 else routine_name
    lw = Lcd.textWidth(short)
    Lcd.setTextColor(_GRAY, _DARK)
    Lcd.drawString(short, (_W - lw) // 2, 62)
    conf = "Y confirm   N cancel"
    cw = Lcd.textWidth(conf)
    Lcd.setTextColor(_ORANGE, _DARK)
    Lcd.drawString(conf, (_W - cw) // 2, 77)


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

def _draw_active(Lcd, exercise, set_num, weight, reps, pr_e1rm, rep_color=None, cur_side=None):
    if cur_side is not None:
        badge = "S{} {}".format(set_num, cur_side)
    else:
        badge = "S{}".format(set_num)
    _draw_header(Lcd, exercise[:18], badge)
    _draw_hint(Lcd, "j/k wt  u/d r  Ent log  L uni  P/N  A R  F")
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

    # Side indicator box (unilateral mode)
    if cur_side is not None:
        if cur_side == "R":
            side_bg = _TEAL
        else:
            side_bg = _ORANGE
        Lcd.fillRect(_W - 26, 94, 24, 20, side_bg)
        Lcd.setTextColor(_BLACK, side_bg)
        Lcd.setTextSize(2)
        Lcd.drawString(cur_side, _W - 20, 97)
        Lcd.setTextSize(1)


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
    routines = _load_routines()

    mode = _MODE_HOME

    # HOME state
    home_cursor = 0
    home_scroll = 0
    delete_idx = None

    # SPLIT state
    split_cursor = 0
    split_scroll = 0

    # Routine management state
    routine_name_buf = []
    routine_name_editing_idx = None  # None = creating; int = index in routines being edited
    routine_pick_pool = []           # all exercises + custom ones + "+" sentinel
    routine_pick_selected = set()    # selected exercises for this routine
    routine_pick_cursor = 0
    routine_pick_scroll = 0
    routine_del_idx = None           # index of routine being deleted

    # Custom exercise text entry
    custom_ex_buf = []

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

    # Unilateral state
    unilateral_exercises = set()   # exercises toggled to unilateral this session
    cur_side = None                # None = bilateral; "R" or "L" when unilateral
    pending_r_weight = 0           # R-arm weight held while doing L arm
    pending_r_reps = 0             # R-arm reps held while doing L arm

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
            raw_key = kb.get_key()
            ch = _normalize_key(raw_key)
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
                        split_scroll = 0
                        mode = _MODE_SPLIT
                        _draw_split(Lcd, routines, split_cursor, split_scroll)
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
                num_routines = len(routines)
                total_items = num_routines + len(_SPLIT_NAMES)
                if ch in (";", ",", "w"):
                    if split_cursor > 0:
                        split_cursor -= 1
                    else:
                        split_cursor = total_items - 1
                    if split_cursor < split_scroll:
                        split_scroll = split_cursor
                    elif split_cursor >= split_scroll + 6:
                        split_scroll = split_cursor - 5
                    _draw_split(Lcd, routines, split_cursor, split_scroll)
                elif ch in (".", "/", "s"):
                    if split_cursor < total_items - 1:
                        split_cursor += 1
                    else:
                        split_cursor = 0
                    if split_cursor < split_scroll:
                        split_scroll = split_cursor
                    elif split_cursor >= split_scroll + 6:
                        split_scroll = split_cursor - 5
                    _draw_split(Lcd, routines, split_cursor, split_scroll)
                elif ch == "\n":
                    if split_cursor < num_routines:
                        # Launch a routine directly
                        routine = routines[split_cursor]
                        ex_list = routine.get("exercises", [])
                        if ex_list:
                            active_exercises = list(ex_list)
                            active_idx = 0
                            set_num = 1
                            cur_weight = 135
                            cur_reps = 5
                            rc = _rc_new()
                            rep_flash_until = 0
                            session = []
                            pending_r_weight = 0
                            pending_r_reps = 0
                            unilateral_exercises = set()
                            cur_side = None
                            first_ex = active_exercises[0]
                            pr_e1rm = _get_pr_e1rm(prs, first_ex, cur_side)
                            split_name = routine.get("name", "Routine")
                            mode = _MODE_ACTIVE
                            _draw_active(Lcd, first_ex, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
                    else:
                        # Launch a built-in split — go to picker as before
                        split_name = _SPLIT_NAMES[split_cursor - num_routines]
                        picker_exercises = list(_SPLIT_EXERCISES[split_name])
                        picker_selected = set()
                        picker_cursor = 0
                        picker_scroll = 0
                        mode = _MODE_PICKER
                        _draw_picker(Lcd, split_name, picker_exercises, picker_selected,
                                     picker_cursor, picker_scroll)
                elif ch == "c":
                    # Create new routine — go to name entry
                    routine_name_buf = []
                    routine_name_editing_idx = None
                    mode = _MODE_ROUTINE_NAME
                    _draw_text_entry(Lcd, "Routine Name", routine_name_buf,
                                     "Type name  Enter next  Q cancel")
                elif ch == "e" and split_cursor < num_routines:
                    # Edit existing routine
                    routine = routines[split_cursor]
                    routine_name_buf = list(routine.get("name", ""))
                    routine_name_editing_idx = split_cursor
                    mode = _MODE_ROUTINE_NAME
                    _draw_text_entry(Lcd, "Routine Name", routine_name_buf,
                                     "Type name  Enter next  Q cancel")
                elif ch == "d" and split_cursor < num_routines:
                    # Delete routine
                    routine_del_idx = split_cursor
                    rname = routines[split_cursor].get("name", "Routine")
                    mode = _MODE_ROUTINE_DEL
                    _draw_routine_del_modal(Lcd, rname)
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
                        pending_r_weight = 0
                        pending_r_reps = 0
                        first_ex = active_exercises[0]
                        if first_ex in unilateral_exercises:
                            cur_side = "R"
                        else:
                            cur_side = None
                        pr_e1rm = _get_pr_e1rm(prs, first_ex, cur_side)
                        mode = _MODE_ACTIVE
                        _draw_active(Lcd, first_ex, set_num, cur_weight,
                                     cur_reps, pr_e1rm, cur_side=cur_side)
                elif ch in ("q", "\x1b"):
                    mode = _MODE_SPLIT
                    _draw_split(Lcd, routines, split_cursor, split_scroll)

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
                pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
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
                    if cur_side == "R":
                        # Right arm done — store pending, switch to left arm
                        pending_r_weight = cur_weight
                        pending_r_reps = cur_reps
                        cur_side = "L"
                        cur_reps = 5
                        rc = _rc_new()
                        rep_flash_until = 0
                        pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                        _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
                    else:
                        # Bilateral OR left arm done — log set(s) and go to rest
                        found = None
                        for ex in session:
                            if ex["name"] == ex_name:
                                found = ex
                                break
                        if found is None:
                            found = {"name": ex_name, "sets": []}
                            session.append(found)

                        if cur_side == "L":
                            # Log R arm then L arm as separate entries
                            found["sets"].append({"weight": pending_r_weight, "reps": pending_r_reps, "side": "R"})
                            found["sets"].append({"weight": cur_weight, "reps": cur_reps, "side": "L"})
                            # PR per arm
                            pr_r = _check_and_save_pr(prs, ex_name, pending_r_weight, pending_r_reps, side="R")
                            pr_l = _check_and_save_pr(prs, ex_name, cur_weight, cur_reps, side="L")
                            new_pr = pr_r or pr_l
                            last_weight = cur_weight
                            last_reps = cur_reps
                            # Reset to right arm for next set
                            cur_side = "R"
                            pending_r_weight = 0
                            pending_r_reps = 0
                        else:
                            # Normal bilateral
                            found["sets"].append({"weight": cur_weight, "reps": cur_reps})
                            new_pr = _check_and_save_pr(prs, ex_name, cur_weight, cur_reps)
                            last_weight = cur_weight
                            last_reps = cur_reps

                        if new_pr:
                            _beep_pr()
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
                        pending_r_weight = 0
                        pending_r_reps = 0
                        if next_name in unilateral_exercises:
                            cur_side = "R"
                        else:
                            cur_side = None
                        pr_e1rm = _get_pr_e1rm(prs, next_name, cur_side)
                        _draw_active(Lcd, next_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
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
                        pending_r_weight = 0
                        pending_r_reps = 0
                        if ex_name in unilateral_exercises:
                            cur_side = "R"
                        else:
                            cur_side = None
                        pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                        _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
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
                        pending_r_weight = 0
                        pending_r_reps = 0
                        if ex_name in unilateral_exercises:
                            cur_side = "R"
                        else:
                            cur_side = None
                        pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                        _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
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
                elif ch == "l":
                    ex_name = active_exercises[active_idx]
                    if ex_name in unilateral_exercises:
                        # Toggle OFF — return to bilateral
                        unilateral_exercises.discard(ex_name)
                        cur_side = None
                        # Discard any pending R arm data
                        pending_r_weight = 0
                        pending_r_reps = 0
                    else:
                        # Toggle ON — start with right arm
                        unilateral_exercises.add(ex_name)
                        cur_side = "R"
                    pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                    _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
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
                                 cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)

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
                        pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                        rc = _rc_new()
                        rep_flash_until = 0
                        mode = _MODE_ACTIVE
                        _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
                elif remaining_ms <= 0:
                    _beep_rest_done()
                    ex_name = active_exercises[active_idx]
                    pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                    rc = _rc_new()
                    rep_flash_until = 0
                    mode = _MODE_ACTIVE
                    _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
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
                    pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                    _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)
                elif ch in ("q", "\x1b"):
                    mode = _MODE_ACTIVE
                    ex_name = active_exercises[active_idx]
                    pr_e1rm = _get_pr_e1rm(prs, ex_name, cur_side)
                    _draw_active(Lcd, ex_name, set_num, cur_weight, cur_reps, pr_e1rm, cur_side=cur_side)

            # ── ROUTINE_NAME ──────────────────────────────────────────────────
            elif mode == _MODE_ROUTINE_NAME:
                rch = _raw_char(raw_key)
                if rch is None:
                    _time.sleep_ms(40)
                    continue
                if rch == "\x08":
                    # Backspace
                    if routine_name_buf:
                        routine_name_buf.pop()
                    _draw_text_entry(Lcd, "Routine Name", routine_name_buf,
                                     "Type name  Enter next  Q cancel")
                elif rch == "\n":
                    if routine_name_buf:
                        # Move to exercise picker
                        base_pool = _all_exercises()
                        # If editing, also include existing custom exercises not in the base pool
                        extra = []
                        if routine_name_editing_idx is not None:
                            existing_exes = routines[routine_name_editing_idx].get("exercises", [])
                            base_set = set(base_pool)
                            for ex in existing_exes:
                                if ex not in base_set:
                                    extra.append(ex)
                        routine_pick_pool = base_pool + extra + ["+"]
                        if routine_name_editing_idx is not None:
                            routine_pick_selected = set(routines[routine_name_editing_idx].get("exercises", []))
                        else:
                            routine_pick_selected = set()
                        routine_pick_cursor = 0
                        routine_pick_scroll = 0
                        mode = _MODE_ROUTINE_PICK
                        _draw_routine_pick(Lcd, routine_pick_pool, routine_pick_selected,
                                           routine_pick_cursor, routine_pick_scroll)
                elif rch == "\x1b":
                    # Cancel — back to split
                    mode = _MODE_SPLIT
                    _draw_split(Lcd, routines, split_cursor, split_scroll)
                else:
                    # Printable char — append if under limit
                    if len(routine_name_buf) < 18:
                        routine_name_buf.append(rch)
                    _draw_text_entry(Lcd, "Routine Name", routine_name_buf,
                                     "Type name  Enter next  Q cancel")

            # ── ROUTINE_PICK ──────────────────────────────────────────────────
            elif mode == _MODE_ROUTINE_PICK:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                num_pool = len(routine_pick_pool)
                if ch in (";", ",", "w"):
                    if routine_pick_cursor > 0:
                        routine_pick_cursor -= 1
                    else:
                        routine_pick_cursor = num_pool - 1
                    if routine_pick_cursor < routine_pick_scroll:
                        routine_pick_scroll = routine_pick_cursor
                    _draw_routine_pick(Lcd, routine_pick_pool, routine_pick_selected,
                                       routine_pick_cursor, routine_pick_scroll)
                elif ch in (".", "/", "s"):
                    if routine_pick_cursor < num_pool - 1:
                        routine_pick_cursor += 1
                    else:
                        routine_pick_cursor = 0
                        routine_pick_scroll = 0
                    if routine_pick_cursor >= routine_pick_scroll + 6:
                        routine_pick_scroll = routine_pick_cursor - 5
                    _draw_routine_pick(Lcd, routine_pick_pool, routine_pick_selected,
                                       routine_pick_cursor, routine_pick_scroll)
                elif ch == " ":
                    item = routine_pick_pool[routine_pick_cursor]
                    if item == "+":
                        # Open custom exercise entry
                        custom_ex_buf = []
                        mode = _MODE_CUSTOM_EX
                        _draw_text_entry(Lcd, "Custom Exercise", custom_ex_buf,
                                         "Type name  Enter add  Q cancel")
                    else:
                        if item in routine_pick_selected:
                            routine_pick_selected.discard(item)
                        else:
                            routine_pick_selected.add(item)
                        _draw_routine_pick(Lcd, routine_pick_pool, routine_pick_selected,
                                           routine_pick_cursor, routine_pick_scroll)
                elif ch == "\n":
                    item = routine_pick_pool[routine_pick_cursor]
                    if item == "+":
                        custom_ex_buf = []
                        mode = _MODE_CUSTOM_EX
                        _draw_text_entry(Lcd, "Custom Exercise", custom_ex_buf,
                                         "Type name  Enter add  Q cancel")
                    elif len(routine_pick_selected) > 0:
                        # Save the routine — preserve order from pool
                        ordered = [e for e in routine_pick_pool if e != "+" and e in routine_pick_selected]
                        rname = "".join(routine_name_buf)
                        if routine_name_editing_idx is not None:
                            routines[routine_name_editing_idx] = {"name": rname, "exercises": ordered}
                        else:
                            routines.append({"name": rname, "exercises": ordered})
                        routines.sort(key=lambda r: r.get("name", ""))
                        _save_routines(routines)
                        split_cursor = 0
                        split_scroll = 0
                        mode = _MODE_SPLIT
                        _draw_split(Lcd, routines, split_cursor, split_scroll)
                    # If 0 selected and not on "+", Enter is a no-op
                elif ch in ("q", "\x1b"):
                    # Back to name entry
                    mode = _MODE_ROUTINE_NAME
                    _draw_text_entry(Lcd, "Routine Name", routine_name_buf,
                                     "Type name  Enter next  Q cancel")

            # ── CUSTOM_EX ─────────────────────────────────────────────────────
            elif mode == _MODE_CUSTOM_EX:
                rch = _raw_char(raw_key)
                if rch is None:
                    _time.sleep_ms(40)
                    continue
                if rch == "\x08":
                    if custom_ex_buf:
                        custom_ex_buf.pop()
                    _draw_text_entry(Lcd, "Custom Exercise", custom_ex_buf,
                                     "Type name  Enter add  Q cancel")
                elif rch == "\n":
                    if custom_ex_buf:
                        new_ex = "".join(custom_ex_buf)
                        # Add to pool before the "+" sentinel and auto-select it
                        insert_pos = len(routine_pick_pool) - 1  # just before "+"
                        routine_pick_pool.insert(insert_pos, new_ex)
                        routine_pick_selected.add(new_ex)
                        # Move cursor to the newly added item
                        routine_pick_cursor = insert_pos
                        if routine_pick_cursor >= routine_pick_scroll + 6:
                            routine_pick_scroll = routine_pick_cursor - 5
                    mode = _MODE_ROUTINE_PICK
                    _draw_routine_pick(Lcd, routine_pick_pool, routine_pick_selected,
                                       routine_pick_cursor, routine_pick_scroll)
                elif rch == "\x1b":
                    mode = _MODE_ROUTINE_PICK
                    _draw_routine_pick(Lcd, routine_pick_pool, routine_pick_selected,
                                       routine_pick_cursor, routine_pick_scroll)
                else:
                    if len(custom_ex_buf) < 18:
                        custom_ex_buf.append(rch)
                    _draw_text_entry(Lcd, "Custom Exercise", custom_ex_buf,
                                     "Type name  Enter add  Q cancel")

            # ── ROUTINE_DEL ───────────────────────────────────────────────────
            elif mode == _MODE_ROUTINE_DEL:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch == "y":
                    if routine_del_idx is not None and 0 <= routine_del_idx < len(routines):
                        del routines[routine_del_idx]
                        _save_routines(routines)
                    routine_del_idx = None
                    split_cursor = 0
                    split_scroll = 0
                    mode = _MODE_SPLIT
                    _draw_split(Lcd, routines, split_cursor, split_scroll)
                elif ch in ("n", "q", "\x1b"):
                    routine_del_idx = None
                    mode = _MODE_SPLIT
                    _draw_split(Lcd, routines, split_cursor, split_scroll)

            _time.sleep_ms(40)

    finally:
        machine.reset()


run()
