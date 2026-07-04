#!/usr/bin/env python3
"""Signal Radar — live Wi-Fi + Bluetooth LE scanner with a 3D web visualization.

Scans via CoreWLAN (Wi-Fi RSSI/channel/band) and CoreBluetooth via bleak (BLE),
serves a Three.js front end from ./static on http://localhost:8765.

Note: macOS redacts SSIDs/BSSIDs unless the host app (your terminal) has
Location Services permission. Everything else (RSSI, channel, band) works
without it.
"""

import asyncio
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8765
STATIC = Path(__file__).parent / "static"
HISTORY_LEN = 60          # RSSI samples kept per signal
STALE_AFTER = 45.0        # seconds without sighting -> shown faded
DROP_AFTER = 150.0        # seconds without sighting -> removed

store_lock = threading.Lock()
wifi_store = {}           # key -> record
ble_store = {}            # address -> record
bt_store = {}             # classic Bluetooth, address -> record
status = {"wifi_scans": 0, "ble_running": False, "ssids_visible": False,
          "location_auth": "unknown", "errors": []}

BAND_NAME = {1: "2.4", 2: "5", 3: "6"}
WIDTH_MHZ = {0: 20, 1: 20, 2: 40, 3: 80, 4: 160}

SECURITY_ENUMS = [  # (CWSecurity value, label) — checked in order, last match wins
    (1, "WEP"), (2, "WPA"), (3, "WPA/WPA2"), (4, "WPA2"), (6, "Dynamic WEP"),
    (7, "WPA Ent"), (8, "WPA/WPA2 Ent"), (9, "WPA2 Ent"),
    (11, "WPA3"), (12, "WPA3 Ent"), (13, "WPA2/WPA3"),
]

BLE_COMPANIES = {
    0x004C: "Apple", 0x0006: "Microsoft", 0x00E0: "Google", 0x0075: "Samsung",
    0x0087: "Garmin", 0x009E: "Bose", 0x00D2: "Dialog", 0x0157: "Xiaomi/Anhui",
    0x038F: "Xiaomi", 0x0059: "Nordic Semi", 0x01DA: "Logitech", 0x012D: "Sony",
    0x0131: "Cypress", 0x02E5: "Espressif", 0x0171: "Amazon", 0x00C4: "LG",
    0x0499: "Ruuvi", 0x0310: "SGL Italia", 0x0397: "Tile", 0x0A69: "Shelly",
}


def channel_to_freq(ch, band):
    if band == 1:
        return 2484 if ch == 14 else 2407 + 5 * ch
    if band == 2:
        return 5000 + 5 * ch
    if band == 3:
        return 5950 + 5 * ch
    return None


def wifi_security(net):
    label = "Open"
    for val, name in SECURITY_ENUMS:
        try:
            if net.supportsSecurity_(val):
                label = name
        except Exception:
            pass
    return label


def wifi_scan_loop():
    import CoreWLAN
    client = CoreWLAN.CWWiFiClient.sharedWiFiClient()
    iface = client.interface()
    if iface is None:
        status["errors"].append("No Wi-Fi interface found")
        return
    while True:
        try:
            nets, err = iface.scanForNetworksWithName_error_(None, None)
            now = time.time()
            if err is not None:
                status["errors"] = [f"Wi-Fi scan error: {err}"]
                time.sleep(8)
                continue
            # Group anonymous networks per (band, channel) sorted by RSSI so
            # redacted entries keep a stable identity across scans.
            grouped = {}
            for n in nets or []:
                ch_obj = n.wlanChannel()
                key = (ch_obj.channelBand(), ch_obj.channelNumber())
                grouped.setdefault(key, []).append(n)
            with store_lock:
                for (band, ch), members in grouped.items():
                    members.sort(key=lambda m: -m.rssiValue())
                    for i, n in enumerate(members):
                        ssid = n.ssid()
                        bssid = n.bssid()
                        if ssid:
                            status["ssids_visible"] = True
                        key = bssid or f"anon-{band}-{ch}-{i}"
                        rec = wifi_store.get(key)
                        if rec is None:
                            rec = {
                                "id": key, "kind": "wifi",
                                "history": deque(maxlen=HISTORY_LEN),
                                "security": wifi_security(n),
                            }
                            wifi_store[key] = rec
                        rec["ssid"] = ssid
                        rec["bssid"] = bssid
                        rec["rssi"] = n.rssiValue()
                        rec["noise"] = n.noiseMeasurement()
                        rec["channel"] = ch
                        rec["band"] = BAND_NAME.get(band, "?")
                        rec["width_mhz"] = WIDTH_MHZ.get(ch_obj.channelWidth(), 20)
                        rec["freq_mhz"] = channel_to_freq(ch, band)
                        rec["last_seen"] = now
                        rec["history"].append([round(now), n.rssiValue()])
                for key in [k for k, r in wifi_store.items()
                            if now - r["last_seen"] > DROP_AFTER]:
                    del wifi_store[key]
            status["wifi_scans"] += 1
            status["errors"] = [e for e in status["errors"] if "Wi-Fi" not in e]
        except Exception as e:
            status["errors"] = [f"Wi-Fi scan error: {e}"]
        time.sleep(4)


def ble_scan_loop():
    from bleak import BleakScanner

    def on_adv(device, adv):
        now = time.time()
        company = None
        for cid in (adv.manufacturer_data or {}):
            company = BLE_COMPANIES.get(cid, f"Company 0x{cid:04X}")
            break
        name = adv.local_name or device.name
        with store_lock:
            rec = ble_store.get(device.address)
            if rec is None:
                rec = {"id": device.address, "kind": "ble",
                       "history": deque(maxlen=HISTORY_LEN)}
                ble_store[device.address] = rec
            if name:
                rec["name"] = name
            rec.setdefault("name", None)
            rec["rssi"] = adv.rssi
            rec["company"] = company or rec.get("company")
            rec["tx_power"] = adv.tx_power
            rec["last_seen"] = now
            hist = rec["history"]
            if not hist or now - hist[-1][0] >= 2:
                hist.append([round(now), adv.rssi])

    async def run():
        scanner = BleakScanner(detection_callback=on_adv)
        await scanner.start()
        status["ble_running"] = True
        while True:
            await asyncio.sleep(5)
            now = time.time()
            with store_lock:
                for key in [k for k, r in ble_store.items()
                            if now - r["last_seen"] > DROP_AFTER]:
                    del ble_store[key]

    try:
        asyncio.run(run())
    except Exception as e:
        status["ble_running"] = False
        status["errors"].append(f"Bluetooth scan error: {e} "
                                "(grant Bluetooth permission to your terminal in "
                                "System Settings > Privacy & Security > Bluetooth)")


def bt_classic_loop():
    """Poll paired classic-Bluetooth devices (headphones, keyboards, ...).
    Only connected devices have a readable signal level."""
    import IOBluetooth
    while True:
        try:
            now = time.time()
            devs = IOBluetooth.IOBluetoothDevice.pairedDevices() or []
            with store_lock:
                for d in devs:
                    if not d.isConnected():
                        continue
                    addr = str(d.addressString())
                    raw = d.rawRSSI()
                    rssi_valid = -100 <= raw <= -1
                    rec = bt_store.get(addr)
                    if rec is None:
                        rec = {"id": "bt-" + addr, "kind": "bt",
                               "history": deque(maxlen=HISTORY_LEN)}
                        bt_store[addr] = rec
                    rec["name"] = str(d.name() or "Bluetooth device")
                    rec["rssi"] = raw if rssi_valid else -55
                    rec["rssi_valid"] = rssi_valid
                    rec["address"] = addr
                    rec["last_seen"] = now
                    if rssi_valid:
                        rec["history"].append([round(now), raw])
                for key in [k for k, r in bt_store.items()
                            if now - r["last_seen"] > DROP_AFTER]:
                    del bt_store[key]
        except Exception as e:
            status["errors"] = [x for x in status["errors"] if "Classic" not in x]
            status["errors"].append(f"Classic Bluetooth error: {e}")
        time.sleep(8)


AUTH_NAMES = {0: "not_determined", 1: "restricted", 2: "denied",
              3: "authorized", 4: "authorized"}


def try_location_auth():
    """Request Location Services permission so CoreWLAN returns SSIDs.
    Pumps the runloop long enough for the macOS prompt to appear and be
    answered. Harmless no-op if already decided."""
    try:
        import CoreLocation
        import Foundation
        st = CoreLocation.CLLocationManager.authorizationStatus()
        status["location_auth"] = AUTH_NAMES.get(st, str(st))
        if st != 0:
            return
        mgr = CoreLocation.CLLocationManager.alloc().init()
        mgr.requestWhenInUseAuthorization()
        print("Waiting for the macOS Location Services prompt — click Allow to")
        print("see Wi-Fi network names (up to 20s) ...")
        deadline = time.time() + 20
        while time.time() < deadline:
            Foundation.NSRunLoop.currentRunLoop().runUntilDate_(
                Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.5))
            st = CoreLocation.CLLocationManager.authorizationStatus()
            if st != 0:
                break
        status["location_auth"] = AUTH_NAMES.get(st, str(st))
        print(f"Location Services: {status['location_auth']}")
    except Exception:
        pass


def snapshot():
    now = time.time()
    with store_lock:
        wifi = []
        for r in wifi_store.values():
            d = dict(r)
            d["history"] = list(r["history"])
            d["stale"] = now - r["last_seen"] > STALE_AFTER
            wifi.append(d)
        ble = []
        for r in ble_store.values():
            d = dict(r)
            d["history"] = list(r["history"])
            d["stale"] = now - r["last_seen"] > STALE_AFTER
            ble.append(d)
        bt = []
        for r in bt_store.values():
            d = dict(r)
            d["history"] = list(r["history"])
            d["stale"] = now - r["last_seen"] > STALE_AFTER
            bt.append(d)
    return {"ts": now, "wifi": wifi, "ble": ble, "bt": bt, "status": status}


MIME = {".html": "text/html", ".js": "text/javascript", ".css": "text/css",
        ".png": "image/png", ".svg": "image/svg+xml"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path.split("?")[0] == "/api/signals":
            body = json.dumps(snapshot()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        rel = self.path.split("?")[0].lstrip("/") or "index.html"
        target = (STATIC / rel).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self.send_error(404)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", MIME.get(target.suffix, "application/octet-stream"))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    try_location_auth()
    threading.Thread(target=wifi_scan_loop, daemon=True).start()
    threading.Thread(target=ble_scan_loop, daemon=True).start()
    threading.Thread(target=bt_classic_loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Signal Radar running at http://localhost:{PORT}")
    print("Ctrl-C to stop. If network names show as 'Hidden', grant Location")
    print("Services to your terminal app in System Settings > Privacy & Security.")
    server.serve_forever()


if __name__ == "__main__":
    main()
