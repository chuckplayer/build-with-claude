"""Workout Tracker — log exercises, sets, weights, reps; track PRs.

Screens:
  MODE_SELECT  — scrollable exercise list
  MODE_SET     — log weight/reps for the current set
  MODE_REST    — countdown timer between sets
  MODE_FINISH  — summary of completed workout
"""

_W, _H = 240, 135
_BLACK  = 0x000000
_ORANGE = 0xCC785C
_CREAM  = 0xF0EEE6
_DARK   = 0x1F1F1F
_GRAY   = 0x777777
_GREEN  = 0x00FF00

_EXERCISES_FILE = "/flash/exercises.json"
_HISTORY_FILE   = "/flash/workout_history.json"
_PRS_FILE       = "/flash/workout_prs.json"
_REST_SECS      = 90
_WEIGHT_STEP    = 5
_MAX_HISTORY    = 20

_DEFAULT_EXERCISES = [
    "Squat", "Bench Press", "Deadlift", "Overhead Press",
    "Barbell Row", "Lat Pulldown", "Dumbbell Curl", "Tricep Pushdown",
    "Leg Press", "Romanian Deadlift", "Pull-Up", "Dip",
    "Incline Bench", "Cable Fly", "Leg Curl", "Calf Raise",
]

# Module-level JSON and time imports (cached after first use by MicroPython)
try:
    import ujson as _json
except ImportError:
    import json as _json

try:
    import utime as _time
except ImportError:
    import time as _time

MODE_SELECT = "sel"
MODE_SET    = "set"
MODE_REST   = "rest"
MODE_FINISH = "fin"


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


# ── Epley estimated 1-rep-max ─────────────────────────────────────────────────

def _e1rm(weight, reps):
    if reps <= 1:
        return weight
    return int(weight * (1 + reps / 30))


# ── JSON helpers ──────────────────────────────────────────────────────────────

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


# ── Data functions ────────────────────────────────────────────────────────────

def _load_exercises():
    data = _load_json(_EXERCISES_FILE, None)
    if data is None:
        data = list(_DEFAULT_EXERCISES)
        _save_json(_EXERCISES_FILE, data)
    return data


def _load_prs():
    return _load_json(_PRS_FILE, {})


def _save_workout(session):
    try:
        t = _time.localtime()
        date_str = "{}-{:02d}-{:02d}".format(t[0], t[1], t[2])
    except Exception:
        date_str = "unknown"
    entry = {"date": date_str, "exercises": session}
    history = _load_json(_HISTORY_FILE, [])
    history.append(entry)
    if len(history) > _MAX_HISTORY:
        history = history[-_MAX_HISTORY:]
    _save_json(_HISTORY_FILE, history)


def _check_and_save_pr(prs, exercise, weight, reps):
    e1 = _e1rm(weight, reps)
    existing = prs.get(exercise, {})
    if e1 > existing.get("e1rm", 0):
        prs[exercise] = {"weight": weight, "reps": reps, "e1rm": e1}
        _save_json(_PRS_FILE, prs)
        return True
    return False


# ── UI helpers ────────────────────────────────────────────────────────────────

def _draw_header(Lcd, text):
    Lcd.fillRect(0, 0, _W, 17, _DARK)
    Lcd.setTextColor(_CREAM, _DARK)
    Lcd.drawString(text, 4, 3)
    Lcd.drawLine(0, 17, _W, 17, _ORANGE)


def _draw_hint(Lcd, text):
    Lcd.fillRect(0, _H - 18, _W, 18, _DARK)
    Lcd.setTextColor(_GRAY, _DARK)
    Lcd.drawString(text, 4, _H - 14)


def _clear_body(Lcd):
    Lcd.fillRect(0, 18, _W, _H - 36, _BLACK)


# ── Screen: exercise select ───────────────────────────────────────────────────

def _draw_exercise_select(Lcd, exercises, cursor, scroll_top):
    _draw_header(Lcd, "Workout Tracker")
    _draw_hint(Lcd, "; . scroll  Enter select  Q exit")
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
            Lcd.setTextColor(_CREAM, _BLACK)
        Lcd.drawString(name, 8, y)
    # scroll indicators
    Lcd.setTextColor(_ORANGE, _BLACK)
    if scroll_top > 0:
        Lcd.drawString("^", 228, 20)
    if scroll_top + 6 < len(exercises):
        Lcd.drawString("v", 228, 20 + 5 * row_h)


# ── Screen: set logging ───────────────────────────────────────────────────────

def _draw_set_screen(Lcd, exercise, set_num, weight, reps, pr_e1rm):
    header = "{} S{}".format(exercise[:18], set_num)
    _draw_header(Lcd, header)
    _draw_hint(Lcd, "j/k weight  u/d reps  Enter log  F finish  Q back")
    _clear_body(Lcd)

    # Weight (large, centered)
    Lcd.setTextSize(2)
    Lcd.setTextColor(_CREAM, _BLACK)
    w_str = "{} lbs".format(weight)
    wx = (_W - Lcd.textWidth(w_str)) // 2
    Lcd.drawString(w_str, wx, 30)
    Lcd.setTextSize(1)

    # Reps (normal, centered, orange)
    Lcd.setTextColor(_ORANGE, _BLACK)
    r_str = "x {} reps".format(reps)
    rx = (_W - Lcd.textWidth(r_str)) // 2
    Lcd.drawString(r_str, rx, 62)

    # PR info
    if pr_e1rm > 0:
        e1 = _e1rm(weight, reps)
        if e1 > pr_e1rm:
            pr_str = "NEW PR!  e1RM {}".format(e1)
            Lcd.setTextColor(_GREEN, _BLACK)
        else:
            pr_str = "PR: {}".format(pr_e1rm)
            Lcd.setTextColor(_GRAY, _BLACK)
        px = (_W - Lcd.textWidth(pr_str)) // 2
        Lcd.drawString(pr_str, px, 82)


# ── Screen: rest (static parts, called once on mode entry) ────────────────────

def _draw_rest_screen(Lcd, last_weight, last_reps, new_pr):
    _draw_header(Lcd, "Rest")
    _draw_hint(Lcd, "any key next set  F finish  Q exit")
    _clear_body(Lcd)
    set_str = "{}lbs x {}".format(last_weight, last_reps)
    Lcd.setTextColor(_GRAY, _BLACK)
    sx = (_W - Lcd.textWidth(set_str)) // 2
    Lcd.drawString(set_str, sx, 28)
    if new_pr:
        Lcd.setTextColor(_GREEN, _BLACK)
        pw = Lcd.textWidth("NEW PR!")
        Lcd.drawString("NEW PR!", (_W - pw) // 2, 44)


# ── Screen: rest timer update (called every ~500 ms) ──────────────────────────

def _draw_rest_timer(Lcd, secs_left):
    Lcd.fillRect(0, 65, _W, 45, _BLACK)
    mins = secs_left // 60
    secs = secs_left % 60
    t_str = "{}:{:02d}".format(mins, secs)
    Lcd.setTextSize(3)
    Lcd.setTextColor(_ORANGE, _BLACK)
    tx = (_W - Lcd.textWidth(t_str)) // 2
    Lcd.drawString(t_str, tx, 70)
    Lcd.setTextSize(1)


# ── Screen: finish summary ────────────────────────────────────────────────────

def _draw_finish_screen(Lcd, session):
    _draw_header(Lcd, "Workout Saved!")
    _draw_hint(Lcd, "Enter new workout  Q exit")
    _clear_body(Lcd)
    total_sets = sum(len(ex["sets"]) for ex in session)
    total_vol = sum(
        s["weight"] * s["reps"]
        for ex in session
        for s in ex["sets"]
    )
    Lcd.setTextColor(_CREAM, _BLACK)
    Lcd.drawString("Sets:   {}".format(total_sets), 8, 24)
    Lcd.drawString("Volume: {} lbs".format(total_vol), 8, 40)
    y = 62
    Lcd.setTextColor(_GRAY, _BLACK)
    for ex in session[-3:]:
        if ex["sets"]:
            last = ex["sets"][-1]
            line = "{}  {}x{}".format(ex["name"][:15], last["weight"], last["reps"])
            Lcd.drawString(line, 8, y)
            y += 16


# ── Finish helper ─────────────────────────────────────────────────────────────

def _do_finish(Lcd, session):
    if session:
        _save_workout(session)
    _draw_finish_screen(Lcd, session)


# ── Main entrypoint ───────────────────────────────────────────────────────────

def run():
    import M5
    Lcd = M5.Lcd
    from hardware import MatrixKeyboard
    import machine

    M5.begin()
    Lcd.setFont(Lcd.FONTS.DejaVu9)
    kb = MatrixKeyboard()

    exercises = _load_exercises()
    prs = _load_prs()

    session = []
    mode = MODE_SELECT

    sel_cursor = 0
    sel_scroll = 0

    cur_exercise = None
    cur_weight = 135
    cur_reps = 5
    set_num = 1

    rest_end_ms = 0
    last_weight = 0
    last_reps = 0
    new_pr = False

    _draw_exercise_select(Lcd, exercises, sel_cursor, sel_scroll)

    try:
        while True:
            M5.update()
            kb.tick()
            ch = _normalize_key(kb.get_key())

            # ── SELECT mode ───────────────────────────────────────────────
            if mode == MODE_SELECT:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch in (";", ",", "w"):
                    if sel_cursor > 0:
                        sel_cursor -= 1
                        if sel_cursor < sel_scroll:
                            sel_scroll = sel_cursor
                    else:
                        sel_cursor = len(exercises) - 1
                        sel_scroll = max(0, sel_cursor - 5)
                    _draw_exercise_select(Lcd, exercises, sel_cursor, sel_scroll)
                elif ch in (".", "/", "s"):
                    if sel_cursor < len(exercises) - 1:
                        sel_cursor += 1
                        if sel_cursor >= sel_scroll + 6:
                            sel_scroll = sel_cursor - 5
                    else:
                        sel_cursor = 0
                        sel_scroll = 0
                    _draw_exercise_select(Lcd, exercises, sel_cursor, sel_scroll)
                elif ch == "\n":
                    cur_exercise = exercises[sel_cursor]
                    set_num = 1
                    # Carry forward last weight used for this exercise this session
                    for ex in reversed(session):
                        if ex["name"] == cur_exercise and ex["sets"]:
                            cur_weight = ex["sets"][-1]["weight"]
                            break
                    pr_e1rm = prs.get(cur_exercise, {}).get("e1rm", 0)
                    mode = MODE_SET
                    _draw_set_screen(Lcd, cur_exercise, set_num, cur_weight, cur_reps, pr_e1rm)
                elif ch in ("q", "\x1b"):
                    break

            # ── SET mode ──────────────────────────────────────────────────
            elif mode == MODE_SET:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                pr_e1rm = prs.get(cur_exercise, {}).get("e1rm", 0)
                redraw = False
                if ch == "k":
                    cur_weight += _WEIGHT_STEP
                    redraw = True
                elif ch == "j":
                    cur_weight = max(0, cur_weight - _WEIGHT_STEP)
                    redraw = True
                elif ch == "u":
                    cur_reps += 1
                    redraw = True
                elif ch == "d":
                    cur_reps = max(1, cur_reps - 1)
                    redraw = True
                elif ch == "\n":
                    # Log the set
                    found = None
                    for ex in session:
                        if ex["name"] == cur_exercise:
                            found = ex
                            break
                    if found is None:
                        found = {"name": cur_exercise, "sets": []}
                        session.append(found)
                    found["sets"].append({"weight": cur_weight, "reps": cur_reps})
                    new_pr = _check_and_save_pr(prs, cur_exercise, cur_weight, cur_reps)
                    last_weight = cur_weight
                    last_reps = cur_reps
                    set_num += 1
                    rest_end_ms = _time.ticks_add(_time.ticks_ms(), _REST_SECS * 1000)
                    mode = MODE_REST
                    _draw_rest_screen(Lcd, last_weight, last_reps, new_pr)
                    _draw_rest_timer(Lcd, _REST_SECS)
                elif ch == "f":
                    _do_finish(Lcd, session)
                    mode = MODE_FINISH
                elif ch in ("q", "\x1b"):
                    mode = MODE_SELECT
                    _draw_exercise_select(Lcd, exercises, sel_cursor, sel_scroll)
                if redraw:
                    _draw_set_screen(Lcd, cur_exercise, set_num, cur_weight, cur_reps, pr_e1rm)

            # ── REST mode ─────────────────────────────────────────────────
            elif mode == MODE_REST:
                remaining_ms = _time.ticks_diff(rest_end_ms, _time.ticks_ms())
                secs_left = max(0, remaining_ms // 1000)
                if ch is not None:
                    if ch == "f":
                        _do_finish(Lcd, session)
                        mode = MODE_FINISH
                    elif ch in ("q", "\x1b"):
                        mode = MODE_SELECT
                        _draw_exercise_select(Lcd, exercises, sel_cursor, sel_scroll)
                    else:
                        # Any other key skips rest → back to set screen
                        pr_e1rm = prs.get(cur_exercise, {}).get("e1rm", 0)
                        mode = MODE_SET
                        _draw_set_screen(Lcd, cur_exercise, set_num, cur_weight, cur_reps, pr_e1rm)
                elif remaining_ms <= 0:
                    pr_e1rm = prs.get(cur_exercise, {}).get("e1rm", 0)
                    mode = MODE_SET
                    _draw_set_screen(Lcd, cur_exercise, set_num, cur_weight, cur_reps, pr_e1rm)
                else:
                    _draw_rest_timer(Lcd, secs_left)
                    _time.sleep_ms(500)
                continue

            # ── FINISH mode ───────────────────────────────────────────────
            elif mode == MODE_FINISH:
                if ch is None:
                    _time.sleep_ms(40)
                    continue
                if ch == "\n":
                    session = []
                    mode = MODE_SELECT
                    _draw_exercise_select(Lcd, exercises, sel_cursor, sel_scroll)
                elif ch in ("q", "\x1b"):
                    break

            _time.sleep_ms(40)

    finally:
        machine.reset()


run()
