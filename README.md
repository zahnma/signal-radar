# Signal Radar

Live 3D visualization of the RF environment around your Mac: every Wi-Fi
network (2.4 / 5 / 6 GHz), Bluetooth LE device, and connected classic
Bluetooth device the built-in radios can hear, plus a 2D Wi-Fi spectrum view
by channel. The legend chips at the top are toggles — click one to show/hide
that signal type everywhere (3D, list, spectrum); the choice persists.

![what it shows] Your Mac sits at the center. Each signal is a glowing orb:

- **Distance from center** = signal strength (inner ring −40 dBm … outer −90 dBm)
- **Height** = frequency band (BLE low, then 2.4 → 5 → 6 GHz shelves)
- **Color** = signal type: blue 2.4 GHz · green 5 GHz · amber 6 GHz · violet BLE · magenta BT Classic
- **Size / glow** = strength; orbs pulse when a fresh reading arrives

Hover any orb for details (channel, security, maker, TX power). The side
panel lists everything by strength; the bottom strip shows Wi-Fi networks as
channel-width bumps across the three bands, like a spectrum analyzer.

## Run

```sh
./run.sh            # starts the server and opens the browser
```

or manually: `.venv/bin/python server.py` then open <http://localhost:8765>.

## Setup (already done if `.venv` exists)

```sh
python3 -m venv .venv
.venv/bin/pip install pyobjc-framework-CoreWLAN pyobjc-framework-CoreLocation \
                      pyobjc-framework-IOBluetooth bleak
```

## macOS permissions

- **Network names ("Hidden network")**: macOS redacts SSIDs unless the
  terminal app has Location Services permission. On startup the server
  requests it and waits up to 20 s — click **Allow** on the macOS prompt.
  If no prompt appears (or you clicked Don't Allow), enable it manually in
  System Settings → Privacy & Security → Location Services → your terminal,
  then restart the server. Names appear on the next scan.
- **Bluetooth**: the first run prompts for Bluetooth access for your
  terminal. If BLE shows nothing, check
  System Settings → Privacy & Security → Bluetooth.

## Honest limitations

A laptop has no antenna array, so the *direction* of each signal is not
measurable — angles in the 3D view are stable per-device but arbitrary.
Distance-from-center is signal strength, not physical distance. Only signals
the Wi-Fi and Bluetooth radios can decode are visible; seeing the raw
spectrum (Zigbee, LTE, microwave leakage, …) needs SDR hardware.
