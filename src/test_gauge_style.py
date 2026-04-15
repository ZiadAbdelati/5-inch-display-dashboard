#!/usr/bin/env python3
"""Test: modify gauge colors and size from stock entries.

Tests which fields can be safely changed:
- Colors (outline, active, inactive)
- Diameter
- Thickness

Uses full stock container with padded JPEG, modifies a few gauges.
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
    txn = "1ae7df64"

    print(f"Loading stock theme {txn}...")
    container, total_len = get_stock_container(txn)
    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
    jpeg_end = JPEG_OFFSET + orig_jpeg_size
    post_jpeg = container[jpeg_end:total_len]

    new_container = bytearray(container[:total_len])

    # Zero ALL widgets first — clean slate
    new_container[0x80:0x1000] = b'\x00' * (0x1000 - 0x80)

    # Dashboard colors
    green = rgb565(139, 195, 74)      # CPU active
    amber = rgb565(255, 167, 38)      # RAM active
    dark_inactive = rgb565(55, 55, 60)  # inactive arc
    dark_outline = rgb565(30, 30, 35)   # near-invisible outline
    white = rgb565(255, 255, 255)

    # Get stock gauge entries to use as templates (need unique sub values)
    stock_widgets = container[0x80:0x1000]

    # Available gauge entries with unique sub values:
    # [0]  sub=5  diam=52  (GAUGE tag=0x01)
    # [18] sub=18 diam=76  (GAUGE tag=0x23)  -- larger!
    # [19] sub=19 diam=76  (GAUGE tag=0x24)  -- larger!
    # [26] sub=1  diam=52  (GAUGE tag=0x2d)
    # [31] sub=3  diam=52  (GAUGE tag=0x33)
    # [34] sub=17 diam=67  (GAUGE tag=0x04)
    # [36] sub=13 diam=52  (GAUGE tag=0x06)
    # [38] sub=4  diam=67  (GAUGE tag=0x07)
    # [43] sub=10 diam=90  (GAUGE tag=0x1a) -- LARGEST!
    # [45] sub=12 diam=52  (GAUGE tag=0x14)
    # [50] sub=7  diam=52  (GAUGE tag=0x34)

    # Use the large gauges (sub=10 diam=90, sub=18/19 diam=76)
    # and try to modify their colors and diameter

    # Test A: Take gauge sub=10 (diam=90), keep size, change colors + position
    g_a = bytearray(stock_widgets[43*64:44*64])  # sub=10, diam=90
    g_a[1] = 0x01  # tag = CPU temp
    struct.pack_into("<H", g_a, 4, 120)   # x = left area
    struct.pack_into("<H", g_a, 6, 150)   # y = center-ish
    # Change colors
    struct.pack_into("<H", g_a, 12, dark_outline)   # outline
    struct.pack_into("<H", g_a, 14, green)           # active
    struct.pack_into("<H", g_a, 18, dark_inactive)   # inactive
    print(f"Test A: stock gauge sub=10, diam=90, GREEN colors, pos (120,150)")

    # Test B: Take same gauge template, change diameter to 120
    g_b = bytearray(stock_widgets[43*64:44*64])  # sub=10, diam=90
    g_b[1] = 0x03  # tag
    g_b[2] = 18    # use sub=18 (different from test A)
    struct.pack_into("<H", g_b, 4, 350)   # x
    struct.pack_into("<H", g_b, 6, 150)   # y
    g_b[8] = 120   # LARGER diameter!
    g_b[10] = 28   # adjust thickness
    struct.pack_into("<H", g_b, 12, dark_outline)
    struct.pack_into("<H", g_b, 14, amber)
    struct.pack_into("<H", g_b, 18, dark_inactive)
    print(f"Test B: modified gauge sub=18, diam=120, AMBER colors, pos (350,150)")

    # Test C: Original gauge sub=10 exactly (no color change), different position
    g_c = bytearray(stock_widgets[43*64:44*64])  # exact copy
    g_c[1] = 0x05  # different tag
    g_c[2] = 19    # sub=19
    struct.pack_into("<H", g_c, 4, 580)   # x
    struct.pack_into("<H", g_c, 6, 150)   # y
    print(f"Test C: exact stock gauge sub=19, original colors, pos (580,150)")

    # Text widgets for values inside gauges
    text_template = stock_widgets[10*64:11*64]  # fonth=24

    t_a = bytearray(text_template)
    t_a[1] = 0x01; struct.pack_into("<H", t_a, 4, 140); struct.pack_into("<H", t_a, 6, 180)

    t_b = bytearray(text_template)
    t_b[1] = 0x03; struct.pack_into("<H", t_b, 4, 380); struct.pack_into("<H", t_b, 6, 180)

    t_c = bytearray(text_template)
    t_c[1] = 0x05; struct.pack_into("<H", t_c, 4, 600); struct.pack_into("<H", t_c, 6, 180)

    # Place in widget table
    widgets = [g_a, g_b, g_c, t_a, t_b, t_c]
    for i, w in enumerate(widgets):
        new_container[0x80 + i*64 : 0x80 + (i+1)*64] = w

    # Replace JPEG (padded)
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    new_container[JPEG_OFFSET:JPEG_OFFSET + len(new_jpeg)] = new_jpeg
    new_container[JPEG_OFFSET + len(new_jpeg):JPEG_OFFSET + orig_jpeg_size] = b'\x00' * padding
    struct.pack_into(">I", new_container, 0x58, total_len)

    print(f"\nContainer: {total_len} bytes, {len(widgets)} widgets")
    chunks, end_packet = chunk_container(bytes(new_container), total_len)
    send_upload(device, chunks, end_packet)

    time.sleep(4)

    print("Sending sensor data...")
    test_data = {0x01: 45, 0x03: 67, 0x05: 82}
    for i in range(5):
        send_sensor_data(device, test_data)
        time.sleep(1.5)

    print("""
Done! Look for 3 gauges side by side:
  LEFT:   Green arc, diam=90 (stock size, custom colors) — 45%
  CENTER: Amber arc, diam=120 (enlarged, custom colors) — 67%
  RIGHT:  Original colors, diam=90 (exact stock copy) — 82%
""")


if __name__ == "__main__":
    main()
