#!/usr/bin/env python3
"""Minimal test: take full working stock widget table, move ONE widget.

Start from the exact padded JPEG test that worked, and change just
one gauge's x/y position. This tells us if position changes work.
"""

import struct
import sys
import time
from io import BytesIO
from PIL import Image
from parser import parse_dms_capture, reconstruct_container
from substitute_jpeg import crc16_modbus, chunk_container, send_upload, JPEG_OFFSET, DEVICE

CAPTURE_FILE = "../captures/2ndtest.txt"


def get_stock_container(txn_id):
    writes = parse_dms_capture(CAPTURE_FILE)
    packets = []
    for d, ts, b in writes:
        if d != "Down":
            continue
        if b[:5] == b"theme" and b[10:14].hex() == txn_id:
            packets.append(b)
        elif b[:3] == b"end" and b[10:14].hex() == txn_id:
            break
    chunks = sorted([p for p in packets if p[:5] == b"theme"], key=lambda p: p[7])
    container = reconstruct_container(chunks)
    total_len = struct.unpack_from(">I", container, 0x58)[0]
    return container, total_len


def make_dark_jpeg():
    img = Image.new("RGB", (800, 480), (18, 18, 22))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85, subsampling=0)
    return buf.getvalue()


def build_0x66(tag_values):
    buf = bytearray(b'\x66\x00\x4d\x01\x1a\x04\x0e\x10\x00\x00\x01\x64')
    for tag in range(0x01, 0x16):
        val = tag_values.get(tag, 0)
        buf += bytes([tag, (val >> 8) & 0xFF, val & 0xFF])
    crc = crc16_modbus(bytes(buf))
    buf += struct.pack('>H', crc)
    return bytes(buf)


def send_sensor_data(device, tag_values):
    import serial
    from substitute_jpeg import wait_for_device
    if not wait_for_device(device):
        return
    packet = build_0x66(tag_values)
    ser = serial.Serial(port=device, baudrate=57600, timeout=1, write_timeout=5)
    ser.rts = True
    ser.dtr = True
    time.sleep(0.05)
    ser.write(packet)
    ser.flush()
    ser.close()
    print(f"  Sent sensor packet")


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    txn = "1ae7df64"

    print(f"Loading stock theme {txn}...")
    container, total_len = get_stock_container(txn)

    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
    jpeg_end = JPEG_OFFSET + orig_jpeg_size
    post_jpeg = container[jpeg_end:total_len]

    # Start with exact stock container content
    new_container = bytearray(container[:total_len])

    # Show original widget positions
    widget_table = new_container[0x80:0x1000]
    print("\nOriginal widget positions:")
    for i in range(len(widget_table) // 64):
        entry = widget_table[i*64:(i+1)*64]
        if all(b == 0 for b in entry):
            continue
        wtype = entry[0]
        tag = entry[1]
        sub = entry[2]
        x = struct.unpack_from("<H", entry, 4)[0]
        y = struct.unpack_from("<H", entry, 6)[0]
        types = {0x92: "GAUGE", 0x93: "TEXT", 0x8b: "BAR", 0x8e: "DT", 0x84: "IMG"}
        print(f"  [{i:2d}] {types.get(wtype,'???')} tag=0x{tag:02x} sub={sub:2d} @ ({x:3d},{y:3d})")

    # MODIFICATION: Move gauge [0] (tag=0x01, sub=5, originally at x=28,y=216)
    # to center of screen (x=374, y=200)
    print("\n--- Modifying gauge [0]: moving from (28,216) to (374,200) ---")
    struct.pack_into("<H", new_container, 0x80 + 4, 374)  # x
    struct.pack_into("<H", new_container, 0x80 + 6, 200)  # y

    # Also move text [10] (tag=0x19, originally at x=260,y=184)
    # to (374, 240) — below the moved gauge
    print("--- Modifying text [10]: moving from (260,184) to (374,240) ---")
    struct.pack_into("<H", new_container, 0x80 + 10*64 + 4, 374)  # x
    struct.pack_into("<H", new_container, 0x80 + 10*64 + 6, 240)  # y

    # Replace JPEG with dark background (padded to original size)
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    new_container[JPEG_OFFSET:JPEG_OFFSET + len(new_jpeg)] = new_jpeg
    new_container[JPEG_OFFSET + len(new_jpeg):JPEG_OFFSET + orig_jpeg_size] = b'\x00' * padding

    # Update totalLen (should be same since we padded)
    struct.pack_into(">I", new_container, 0x58, total_len)

    print(f"\nContainer: {total_len} bytes (same as original)")

    chunks, end_packet = chunk_container(bytes(new_container), total_len)
    send_upload(device, chunks, end_packet)

    print("\nWaiting 4 seconds for theme to load...")
    time.sleep(4)

    print("Sending sensor data (multiple rounds)...")
    test_data = {
        0x01: 45, 0x02: 30, 0x03: 67, 0x04: 1200,
        0x05: 55, 0x06: 40, 0x07: 82, 0x08: 900,
        0x09: 50, 0x0a: 8192, 0x0b: 4096, 0x0c: 67,
        0x0d: 38, 0x11: 55, 0x12: 150, 0x13: 300, 0x14: 50,
    }
    for i in range(5):
        send_sensor_data(device, test_data)
        time.sleep(1.5)

    print("\nDone! Look for:")
    print("  - Gauge [0] should appear at CENTER of screen (was top-left)")
    print("  - Text [10] should appear just below it")
    print("  - All other widgets should be at their original positions")


if __name__ == "__main__":
    main()
