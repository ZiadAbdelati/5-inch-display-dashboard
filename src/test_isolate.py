#!/usr/bin/env python3
"""Isolate which field changes break rendering.

Start from the working padded JPEG theme (all stock widgets intact).
Test ONE change at a time:
  Test 1: Change only color bytes on gauge [0]
  Test 2: Change only tag byte on text [3]
  Test 3: Move a few widgets off-screen (x=900)

Run with argument: 1, 2, 3, or 0 (baseline - no changes)
"""

import struct
import sys
import time
from io import BytesIO
from PIL import Image
from parser import parse_dms_capture, reconstruct_container
from substitute_jpeg import crc16_modbus, chunk_container, send_upload, JPEG_OFFSET, DEVICE

CAPTURE_FILE = "../captures/2ndtest.txt"


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


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


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    test = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    txn = "1ae7df64"
    container, total_len = get_stock_container(txn)
    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]

    # Start from exact stock container
    c = bytearray(container[:total_len])

    # Replace JPEG with dark background (padded)
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    c[JPEG_OFFSET:JPEG_OFFSET + len(new_jpeg)] = new_jpeg
    c[JPEG_OFFSET + len(new_jpeg):JPEG_OFFSET + orig_jpeg_size] = b'\x00' * padding

    if test == 0:
        print("TEST 0: Baseline — padded JPEG, NO widget changes")

    elif test == 1:
        print("TEST 1: Change ONLY gauge [0] colors (keep x/y/tag/sub/diam)")
        off = 0x80  # widget [0]
        green = rgb565(139, 195, 74)
        dark = rgb565(55, 55, 60)
        struct.pack_into("<H", c, off + 12, dark)    # outline
        struct.pack_into("<H", c, off + 14, green)    # active
        struct.pack_into("<H", c, off + 18, dark)     # inactive
        print(f"  Changed bytes [12:14] outline, [14:16] active, [18:20] inactive")

    elif test == 2:
        print("TEST 2: Change ONLY text [3] tag from 0x0b to 0x01")
        off = 0x80 + 3 * 64
        old_tag = c[off + 1]
        c[off + 1] = 0x01
        print(f"  Changed tag: 0x{old_tag:02x} -> 0x01")

    elif test == 3:
        print("TEST 3: Move widgets [1],[2],[4] off-screen (BARs)")
        for idx in [1, 2, 4]:
            off = 0x80 + idx * 64
            struct.pack_into("<H", c, off + 4, 900)
            struct.pack_into("<H", c, off + 6, 600)
            print(f"  Widget [{idx}] moved to (900,600)")

    elif test == 4:
        print("TEST 4: Change gauge [0] x/y only (move to center)")
        off = 0x80
        struct.pack_into("<H", c, off + 4, 400)
        struct.pack_into("<H", c, off + 6, 240)
        print(f"  Gauge [0] moved to (400,240)")

    struct.pack_into(">I", c, 0x58, total_len)

    chunks, end_packet = chunk_container(bytes(c), total_len)
    send_upload(device, chunks, end_packet)

    time.sleep(4)

    print("Sending sensor data...")
    test_data = {
        0x01: 45, 0x02: 30, 0x03: 67, 0x04: 1200,
        0x05: 55, 0x06: 40, 0x07: 82, 0x08: 900,
        0x0a: 8192, 0x0b: 4096, 0x0c: 67, 0x0d: 38,
        0x11: 55, 0x12: 150, 0x13: 300, 0x14: 50,
    }
    for i in range(5):
        send_sensor_data(device, test_data)
        time.sleep(1.5)

    print("Done!")


if __name__ == "__main__":
    main()
