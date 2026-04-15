#!/usr/bin/env python3
"""Incremental test: modify 4 gauges (color + position + tag), no hiding."""

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


def modify(c, idx, tag=None, x=None, y=None,
           outline=None, active=None, inactive=None):
    off = 0x80 + idx * 64
    if tag is not None: c[off + 1] = tag
    if x is not None: struct.pack_into("<H", c, off + 4, x)
    if y is not None: struct.pack_into("<H", c, off + 6, y)
    if outline is not None: struct.pack_into("<H", c, off + 12, outline)
    if active is not None: struct.pack_into("<H", c, off + 14, active)
    if inactive is not None: struct.pack_into("<H", c, off + 18, inactive)


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    txn = "1ae7df64"

    container, total_len = get_stock_container(txn)
    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
    c = bytearray(container[:total_len])

    # Replace JPEG
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    c[JPEG_OFFSET:JPEG_OFFSET + len(new_jpeg)] = new_jpeg
    c[JPEG_OFFSET + len(new_jpeg):JPEG_OFFSET + orig_jpeg_size] = b'\x00' * padding

    green = rgb565(139, 195, 74)
    amber = rgb565(255, 167, 38)
    dark = rgb565(55, 55, 60)
    dk_outline = rgb565(30, 30, 35)

    # Modify 4 gauges: change tag + position + colors
    # Place them in a row across the middle of the screen
    gauges = [
        (43, 0x01, 100, 200),   # diam=90
        (18, 0x03, 280, 200),   # diam=76
        (19, 0x05, 460, 200),   # diam=76
        (38, 0x07, 640, 200),   # diam=67
    ]

    for idx, tag, x, y in gauges:
        color = green if tag in (0x01, 0x05) else amber
        modify(c, idx, tag=tag, x=x, y=y,
               outline=dk_outline, active=color, inactive=dark)
        diam = c[0x80 + idx*64 + 8]
        print(f"Gauge [{idx:2d}]: tag=0x{tag:02x}, diam={diam}, @ ({x},{y})")

    # DON'T hide anything — all other widgets stay at original positions

    struct.pack_into(">I", c, 0x58, total_len)
    chunks, end_packet = chunk_container(bytes(c), total_len)
    send_upload(device, chunks, end_packet)

    time.sleep(4)

    test_data = {
        0x01: 17, 0x03: 27, 0x05: 32, 0x07: 48,
        # Keep original tags fed too
        0x02: 30, 0x04: 1200, 0x06: 40, 0x08: 900,
        0x0a: 8192, 0x0b: 4096, 0x0c: 67, 0x0d: 38,
        0x11: 55, 0x12: 150, 0x13: 300, 0x14: 50,
    }
    for i in range(5):
        send_sensor_data(device, test_data)
        time.sleep(1.5)

    print("""
Done! 4 gauges repositioned in a row across y=200:
  Green(17%) | Amber(27%) | Green(32%) | Amber(48%)
All other stock widgets still visible at original positions.
""")


if __name__ == "__main__":
    main()
