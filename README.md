# m5stack

Flash a Cardputer-Adv and install the app bundle in one command.

## Quick start

1. Clone this repo locally — anywhere is fine:
   ```bash
   git clone <repo-url>
   ```
   The skill auto-detects the buddy bundle relative to its own install location, so the clone path doesn't matter. `~/Downloads/m5stack/` and `~/Desktop/m5stack/` are also checked as conventional fallbacks.
2. Plug the Cardputer into your laptop via USB-C
3. Open Claude Code and start a new chat
4. Point Claude Code to the repo folder
5. Type `m5-onboard go`

That's it — Claude will automatically flash the firmware and push the apps onto the device.

### When Claude prompts you to put the device into download mode

Halfway through, Claude will pause and ask you to do this on the **back** of the device:

1. Hold down the **G0** button on the Cardputer
2. While still holding G0, press the **Reset** button
3. Release Reset first, then release G0
4. The screen goes dark — device is in download mode

Claude takes over from there.

### What happens next

- **Firmware writes to the device** (~180 seconds)
- **Apps push to the device** (~100 seconds)
- **Device reboots** straight into the launcher — pick an app and go

Done. Power the device on/off with the side switch.

---

## Launcher apps

After boot the launcher shows a menu with pinned apps at the top and a **Utilities** submenu for tools.

### Pinned apps

| App | What it does |
|-----|-------------|
| **Workout Tracker** | Log sets by split (Push/Pull/Legs/Full Body); live accelerometer rep counting; PR tracking with Epley e1RM; 90 s rest timer with beep |

### Utilities submenu

| App | What it does |
|-----|-------------|
| **File Browser** | Navigate `/flash/`, view text files, delete files |
| **System Info** | One-screen snapshot of firmware, memory, storage, network, and battery state (R to refresh) |
| **WiFi Browser** | Scan networks, type a password, connect, and save the credential for future boots |

### Other apps

| App | What it does |
|-----|-------------|
| **Timer** | Countdown timer and stopwatch; T toggles mode, Enter starts/pauses/resumes, R resets |

### Workout Tracker keys

| Screen | Key | Action |
|--------|-----|--------|
| Home | Enter | Start new workout |
| Home | D | Delete selected history entry |
| Split | Enter | Select split (Push / Pull / Legs / Full Body) |
| Picker | Space | Toggle exercise selection |
| Picker | Enter | Begin workout with selected exercises |
| Active | j / k | Weight −5 / +5 lbs |
| Active | u / d | Reps up / down |
| Active | Enter | Log the set, start rest timer |
| Active | P / N | Previous / next exercise |
| Active | A | Add an exercise from the split list |
| Active | R | Remove current exercise from queue |
| Active | F | Finish and save workout |
| Rest | any key | Skip rest, return to set screen |
| Rest | F | Finish workout |

The accelerometer rep counter activates automatically during a set — place the device flat on the weight stack. One "out and back" movement = 1 rep. Manual u/d keys override the count at any time.

## WiFi

On every boot the launcher tries to connect to WiFi before showing the menu. It checks two credential sources in order:

1. **Saved credentials** — written by the WiFi Browser app to `/flash/wifi_saved.json`. These are tried first with a 4 s timeout.
2. **Event credentials** — hardcoded in [`buddy/device/wifi_event.py`](buddy/device/wifi_event.py) (`cardputer` / `cardconnect`). Tried only if the saved credential differs or doesn't exist.

The header strip shows signal bars + SSID on a successful connect, or `offline` on failure. The launcher always continues regardless.

**Connecting to a network:** pick **WiFi Browser** from the launcher, select a network, enter the password, and connect. The credential is saved automatically and used from the next boot onward.

**Using outside the event:** either use WiFi Browser to save your own network, or edit `wifi_event.py` (replace `SSID`/`PASSWORD`) to change the fallback.

## Adding your own app

1. Drop a `.py` file into `buddy/device/apps/`
2. Push just the apps without re-flashing:
   ```bash
   python3 .claude/skills/m5-onboard/scripts/install_apps.py --port <PORT> --src buddy
   ```
3. The launcher auto-discovers the new app on next boot

Use `buddy/device/apps/timer.py` or `buddy/device/apps/system_info.py` as starting templates — both show the standard conventions (keyboard polling, font, exit via `machine.reset()`). For a more complete state-machine example see `buddy/device/apps/workout_tracker.py`.

## Getting back to stock UIFlow

The buddy bundle takes over the boot flow via `/flash/main.py`. Remove
that file and UIFlow's stock launcher boots normally on the next reset.
From the device REPL:

```python
import os
os.remove('/flash/main.py')
import machine; machine.reset()
```

To also drop the apps under `/flash/apps/`, walk that directory the
same way and remove what you don't want.

If you want a fresh UIFlow firmware on top, re-run `m5-onboard go`
_without_ `--apps`: the skill flashes UIFlow and stops, leaving the
filesystem alone. The previous `boot_uiflow.py`-rename procedure here
referred to a backup that `install_apps.py` only creates when the
bundle ships its own root `boot.py`; the buddy bundle doesn't, so
that backup never exists for these users.

---

## Prerequisites

You need **Python 3.10+**, **git**, and **Claude Code** on your laptop. `pyserial` ships vendored inside `.claude/skills/m5-onboard/scripts/vendor/`. `esptool` is GPL-licensed and is **not** vendored — the skill auto-installs it via pip on first run if it isn't already in your environment, so the user-facing experience is still a single command. To pre-install explicitly: `python3 -m pip install --user -r requirements.txt`.

Bootstrap if needed:

- **macOS** — `python3` usually pre-installed; if not, `brew install python`
- **Linux (Debian/Ubuntu)** — `sudo apt-get install -y python3 python3-pip git`
- **Windows** — `winget install -e --id Python.Python.3.13` and `winget install -e --id Git.Git`

**Windows + older boards only:** the CH9102 USB-UART driver is needed for Basic / Fire / Core2 / StickC. Download from [WCH](https://www.wch.cn/downloads/CH343SER_EXE.html). Cardputer-Adv and CoreS3 use the in-box composite-USB driver and need nothing extra.

**Want `--apps buddy` to point at a different bundle?** The default resolves to the `buddy/device/` directory next to the skill in this repo, with `~/Downloads/m5stack/` and `~/Desktop/m5stack/` checked as fallbacks. To override (e.g. you maintain a fork or have a customized bundle elsewhere), set `M5_BUDDY_DIR`:

```bash
export M5_BUDDY_DIR=/path/to/buddy/device
```

## Troubleshooting

- **Download-mode prompt keeps retrying** — you're releasing G0 too early. Release Reset first, keep holding G0 for about a second, then release.
- **"No USB-UART bridge found" (older boards)** — install the CH9102 driver on Windows; on macOS/Linux, unplug and replug.
- **Claude Buddy never connects over BLE** — make sure the buddy launcher (not UIFlow's) owns `/flash/main.py`. The skill handles this automatically on install.
- **Something else feels broken** — run `python3 .claude/skills/m5-onboard/scripts/smoke_test.py --port <PORT>` for an I2C + LCD + speaker + button check.

## What's in this repo

- **`.claude/skills/m5-onboard/`** — the Claude Code skill. Detect port, flash UIFlow, install apps. See [`.claude/skills/m5-onboard/SKILL.md`](.claude/skills/m5-onboard/SKILL.md) for the full playbook and every gotcha baked into the scripts.
- **`buddy/`** — the MicroPython app bundle that gets installed. See [`buddy/README.md`](buddy/README.md) for device-side layout and iteration tooling.

The two are decoupled by design: the `m5-onboard` skill can install any bundle via `--apps <path>`; `buddy` is just what ships here.

## License

This project's own code is licensed under **Apache 2.0** — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).

`pyserial` (BSD-3-Clause, Apache-compatible) is the only third-party package bundled in `.claude/skills/m5-onboard/scripts/vendor/`. `esptool` (GPLv2+) is intentionally not vendored; it's declared as a pip dependency in [`requirements.txt`](requirements.txt) so the repository itself stays cleanly Apache-2.0. See [`LICENSE-THIRD-PARTY.md`](LICENSE-THIRD-PARTY.md) for details.
