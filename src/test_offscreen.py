#!/usr/bin/env python3
"""Test: keep stock widget entries at original indices, hide unwanted off-screen.

Hypothesis: firmware looks up resources by widget table index, not by sub field.
So we must keep entries at their original positions and only change x/y/tag.
Unwanted widgets get moved off-screen (x=900).
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


def rgb565(r, g, b):
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    txn = "1ae7df64"

    print(f"Loading stock theme {txn}...")
    container, total_len = get_stock_container(txn)
    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
    jpeg_end = JPEG_OFFSET + orig_jpeg_size
    post_jpeg = container[jpeg_end:total_len]

    # Start with exact stock container
    new_container = bytearray(container[:total_len])

    # Available stock widgets by index:
    # GAUGES (10 total):
    #  [0]  sub=5  tag=0x01 diam=52  @ (28,216)
    #  [18] sub=18 tag=0x23 diam=76  @ (316,184)
    #  [19] sub=19 tag=0x24 diam=76  @ (390,186)
    #  [26] sub=1  tag=0x2d diam=52  @ (134,64)
    #  [27] sub=20 tag=0x2f diam=52  @ (387,113)
    #  [31] sub=3  tag=0x33 diam=52  @ (154,106)
    #  [34] sub=17 tag=0x04 diam=67  @ (401,23)
    #  [36] sub=13 tag=0x06 diam=52  @ (129,237)
    #  [38] sub=4  tag=0x07 diam=67  @ (341,117)
    #  [43] sub=10 tag=0x1a diam=90  @ (105,101)  <-- LARGEST
    #  [45] sub=12 tag=0x14 diam=52  @ (152,140)
    #  [50] sub=7  tag=0x34 diam=52  @ (417,72)

    # TEXT (large font, fonth=24):
    #  [10] tag=0x19 fonth=24 @ (260,184)
    # TEXT (medium font, fonth=19):
    #  [5]  tag=0x13 fonth=19 @ (334,115)
    #  [13] tag=0x1d fonth=19 @ (312,25)
    # TEXT (fonth=22):
    #  [11] tag=0x1b fonth=22 @ (118,191)

    # Plan: Use 3 gauges + 3 texts for a row of CPU meters
    # Keep at original table indices, change x/y/tag, hide everything else

    # Gauge indices to KEEP (largest available): [43] diam=90, [18] diam=76, [19] diam=76
    # Text indices to KEEP: [10] fonth=24, [5] fonth=19, [11] fonth=22

    keep_indices = {43, 18, 19, 10, 5, 11}

    # Move all OTHER widgets off-screen
    widget_table_start = 0x80
    for i in range(51):
        if i not in keep_indices:
            offset = widget_table_start + i * 64
            entry = new_container[offset:offset+64]
            if any(b != 0 for b in entry):
                # Move off-screen
                struct.pack_into("<H", new_container, offset + 4, 900)  # x off-screen
                struct.pack_into("<H", new_container, offset + 6, 600)  # y off-screen

    # Now position our kept widgets:

    # Gauge [43] (diam=90) → center-left, tag=0x01 (CPU)
    off = widget_table_start + 43 * 64
    new_container[off + 1] = 0x01  # tag
    struct.pack_into("<H", new_container, off + 4, 120)  # x
    struct.pack_into("<H", new_container, off + 6, 130)  # y
    # Try changing colors
    green = rgb565(139, 195, 74)
    dark = rgb565(55, 55, 60)
    dark_outline = rgb565(30, 30, 35)
    struct.pack_into("<H", new_container, off + 12, dark_outline)  # outline
    struct.pack_into("<H", new_container, off + 14, green)          # active
    struct.pack_into("<H", new_container, off + 18, dark)           # inactive
    print(f"Gauge [43]: diam=90, green, @ (120,130), tag=0x01")

    # Gauge [18] (diam=76) → center, tag=0x03
    off = widget_table_start + 18 * 64
    new_container[off + 1] = 0x03
    struct.pack_into("<H", new_container, off + 4, 350)
    struct.pack_into("<H", new_container, off + 6, 140)
    amber = rgb565(255, 167, 38)
    struct.pack_into("<H", new_container, off + 12, dark_outline)
    struct.pack_into("<H", new_container, off + 14, amber)
    struct.pack_into("<H", new_container, off + 18, dark)
    print(f"Gauge [18]: diam=76, amber, @ (350,140), tag=0x03")

    # Gauge [19] (diam=76) → right, tag=0x05
    off = widget_table_start + 19 * 64
    new_container[off + 1] = 0x05
    struct.pack_into("<H", new_container, off + 4, 580)
    struct.pack_into("<H", new_container, off + 6, 140)
    struct.pack_into("<H", new_container, off + 12, dark_outline)
    struct.pack_into("<H", new_container, off + 14, green)
    struct.pack_into("<H", new_container, off + 18, dark)
    print(f"Gauge [19]: diam=76, green, @ (580,140), tag=0x05")

    # Text [10] (fonth=24) → below gauge [43], tag=0x01
    off = widget_table_start + 10 * 64
    new_container[off + 1] = 0x01
    struct.pack_into("<H", new_container, off + 4, 145)
    struct.pack_into("<H", new_container, off + 6, 165)
    print(f"Text [10]: fonth=24, @ (145,165), tag=0x01")

    # Text [5] (fonth=19) → below gauge [18], tag=0x03
    off = widget_table_start + 5 * 64
    new_container[off + 1] = 0x03
    struct.pack_into("<H", new_container, off + 4, 375)
    struct.pack_into("<H", new_container, off + 6, 175)
    print(f"Text [5]: fonth=19, @ (375,175), tag=0x03")

    # Text [11] (fonth=22) → below gauge [19], tag=0x05
    off = widget_table_start + 11 * 64
    new_container[off + 1] = 0x05
    struct.pack_into("<H", new_container, off + 4, 605)
    struct.pack_into("<H", new_container, off + 6, 170)
    print(f"Text [11]: fonth=22, @ (605,170), tag=0x05")

    # Replace JPEG (padded)
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    new_container[JPEG_OFFSET:JPEG_OFFSET + len(new_jpeg)] = new_jpeg
    new_container[JPEG_OFFSET + len(new_jpeg):JPEG_OFFSET + orig_jpeg_size] = b'\x00' * padding
    struct.pack_into(">I", new_container, 0x58, total_len)

    print(f"\nSending {total_len} bytes...")
    chunks, end_packet = chunk_container(bytes(new_container), total_len)
    send_upload(device, chunks, end_packet)

    time.sleep(4)

    print("Sending sensor data...")
    test_data = {0x01: 45, 0x03: 67, 0x05: 82}
    for i in range(6):
        send_sensor_data(device, test_data)
        time.sleep(1.5)

    print("""
Done! Expected:
  3 gauges (green/amber/green) with values 45/67/82
  Text widgets below each showing the numbers
  All other widgets hidden off-screen
""")


if __name__ == "__main__":
    main()
