#!/usr/bin/env python3
"""Test: is the 0x8e 'fan' widget animation sensor-data-driven?

Theme d6b9a480 has two 0x8e widgets at positions matching the CPU/GPU fan
icons, tagged 0x0e (CPU fan) and 0x0f (GPU fan). If the animation is
data-driven, varying the RPM values should spin the fan (or change its
visual state). If it does nothing, the widget is purely pre-programmed
(or we haven't figured out the driving input yet).

Protocol: upload theme d6b9a480 as-is, then stream 0x66 sensor packets
with fan RPM values cycling through a range over ~60 seconds.
"""

import struct
import sys
import time
from parser import parse_dms_capture, reconstruct_container
from substitute_jpeg import (
    crc16_modbus,
    chunk_container,
    send_upload,
    wait_for_device,
    DEVICE,
)
import serial

CAPTURE_FILE = "../captures/2ndtest.txt"
THEME_TXN = "1ae7df64"

CPU_FAN_TAG = 0x17
GPU_FAN_TAG = 0x18


def get_container(txn_id):
    writes = parse_dms_capture(CAPTURE_FILE)
    packets = []
    for d, _ts, b in writes:
        if d != "Down":
            continue
        if b[:5] == b"theme" and b[10:14].hex() == txn_id:
            packets.append(b)
        elif b[:3] == b"end" and b[10:14].hex() == txn_id:
            break
    chunks = sorted([p for p in packets if p[:5] == b"theme"], key=lambda p: p[7])
    return reconstruct_container(chunks)


def build_0x66(tag_values):
    buf = bytearray(b'\x66\x00\x4d\x01\x1a\x04\x0e\x10\x00\x00\x01\x64')
    for tag in range(0x01, 0x16):
        val = tag_values.get(tag, 0)
        buf += bytes([tag, (val >> 8) & 0xFF, val & 0xFF])
    crc = crc16_modbus(bytes(buf))
    buf += struct.pack('>H', crc)
    return bytes(buf)


def send_sensor(device, tag_values):
    if not wait_for_device(device):
        return
    pkt = build_0x66(tag_values)
    ser = serial.Serial(port=device, baudrate=57600, timeout=1, write_timeout=5)
    ser.rts = True
    ser.dtr = True
    time.sleep(0.03)
    ser.write(pkt)
    ser.flush()
    ser.close()


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE

    print(f"Uploading theme {THEME_TXN} (fan widgets at tags 0x0e/0x0f)...")
    container = get_container(THEME_TXN)
    total_len = struct.unpack_from(">I", container, 0x58)[0]
    chunks, end_packet = chunk_container(container, total_len)
    send_upload(device, chunks, end_packet)

    print("\nWaiting 3s for screen to settle...")
    time.sleep(3)

    # Baseline values for the dashboard fields (so the rest of the theme
    # looks alive, not zeroed). Tags 0x01-0x15 per stock sensor format.
    base = {
        0x01: 55,    # CPU temp
        0x02: 50,    # GPU temp
        0x03: 67,    # CPU usage
        0x04: 82,    # GPU usage
        0x05: 8192,  # RAM used
        0x06: 38,    # disk temp
        0x07: 54,    # disk usage
        0x08: 17,    # upload
        0x09: 234,   # download
        0x0a: 4096,  # RAM free
        0x0b: 67,    # RAM usage %
        0x0c: 2800,  # ?
        0x0d: 3200,  # ?
        0x10: 55,    # ?
        0x11: 150,   # ?
        0x12: 300,   # ?
        0x13: 40,    # ?
        0x14: 50,    # volume
        0x15: 0,
    }

    # Phase 1: fans at 0 RPM (10s)
    print("\n[Phase 1] Fan RPM = 0 for 10s (should show static/no animation)")
    vals = dict(base)
    vals[CPU_FAN_TAG] = 0
    vals[GPU_FAN_TAG] = 0
    for _ in range(10):
        send_sensor(device, vals)
        time.sleep(1.0)

    # Phase 2: low RPM (10s)
    print("[Phase 2] Fan RPM = 500 for 10s")
    vals[CPU_FAN_TAG] = 500
    vals[GPU_FAN_TAG] = 500
    for _ in range(10):
        send_sensor(device, vals)
        time.sleep(1.0)

    # Phase 3: mid RPM (10s)
    print("[Phase 3] Fan RPM = 1500 for 10s")
    vals[CPU_FAN_TAG] = 1500
    vals[GPU_FAN_TAG] = 1500
    for _ in range(10):
        send_sensor(device, vals)
        time.sleep(1.0)

    # Phase 4: high RPM (10s)
    print("[Phase 4] Fan RPM = 3000 for 10s")
    vals[CPU_FAN_TAG] = 3000
    vals[GPU_FAN_TAG] = 3000
    for _ in range(10):
        send_sensor(device, vals)
        time.sleep(1.0)

    # Phase 5: ramping continuously (20s)
    print("[Phase 5] Smooth ramp 0 -> 4000 over 20s")
    for i in range(40):
        rpm = int(i / 40 * 4000)
        vals[CPU_FAN_TAG] = rpm
        vals[GPU_FAN_TAG] = rpm
        send_sensor(device, vals)
        time.sleep(0.5)

    print("\nDone. Observations to report:")
    print("  - Did the fan icon appear at all (vs black rectangle)?")
    print("  - Did it rotate/animate at any phase?")
    print("  - Did rotation speed vary with RPM value?")


if __name__ == "__main__":
    main()
