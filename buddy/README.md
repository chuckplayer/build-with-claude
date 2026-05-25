# buddy

MicroPython app bundle for the M5Stack Cardputer-Adv. Installed onto `/flash/` by the [`m5-onboard`](../onboard/) skill — see the [monorepo README](../README.md) for the end-to-end flow.

## What's on the device

```
/flash/
├── main.py              launcher menu (replaces UIFlow's boot flow)
├── wifi_event.py        event-WiFi fallback credentials
└── apps/
    ├── file_browser.py  browse /flash/, view text files, delete files
    ├── system_info.py   firmware / memory / storage / network / battery snapshot
    ├── timer.py         countdown timer and stopwatch
    └── wifi_browser.py  scan networks, connect, save credentials
```

`main.py` scans `/flash/apps/` at boot and builds the menu automatically.  Drop a new `.py` in there, re-run `m5-onboard go` (or `install_apps.py --src buddy`), and it shows up.

### Utilities submenu

`system_info`, `file_browser`, and `wifi_browser` are grouped under a **Utilities** entry at the bottom of the main menu.  Select **Utilities** to open the submenu; press **ESC** (or **Q**) to return to the top level.

## Iterating on device code

`scripts/` has dev tooling for editing device sources without re-running the full onboard flow:

```bash
# Push a subset of files over USB-serial
python3 scripts/push.py --port /dev/cu.usbmodem1101 --files apps/timer.py

# Watch device logs
python3 scripts/tail_serial.py --port /dev/cu.usbmodem1101

# One-shot REPL exec
python3 scripts/repl_run.py --port /dev/cu.usbmodem1101 --script "import os; print(os.listdir('/flash'))"
```

## License

Apache 2.0 — see the [root LICENSE](../LICENSE) and [LICENSE-THIRD-PARTY.md](../LICENSE-THIRD-PARTY.md).
