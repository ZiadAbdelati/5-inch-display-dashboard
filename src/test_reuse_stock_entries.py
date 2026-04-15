#!/usr/bin/env python3
"""Test: take exact stock widget entries, only modify x/y/tag.

The previous test failed because custom widget entries don't reference
valid resource data. This test copies byte-exact entries from the working
stock theme and only changes position and sensor tag routing.
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


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    txn = "1ae7df64"

    print(f"Loading stock theme {txn}...")
    container, total_len = get_stock_container(txn)

    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
    jpeg_end = JPEG_OFFSET + orig_jpeg_size
    post_jpeg = container[jpeg_end:total_len]

    # Extract stock widget table
    stock_widgets = container[0x80:0x1000]

    # Get specific stock widget entries (byte-exact copies)
    def get_stock_widget(idx):
        """Get widget entry at index idx from stock table."""
        return bytearray(stock_widgets[idx*64:(idx+1)*64])

    # Stock theme 1ae7df64 widgets:
    # [0] GAUGE tag=0x01 sub=5  x=28  y=216 diam=52  — green gauge
    # [1] BAR   tag=0x08 sub=7  x=65  y=132
    # [2] BAR   tag=0x09 sub=3  x=322 y=99
    # [3] TEXT  tag=0x0b sub=0  x=80  y=224 fonth=18
    # [4] BAR   tag=0x10 sub=12 x=59  y=169
    # [5] TEXT  tag=0x13 sub=0  x=334 y=115 fonth=19
    # [6] TEXT  tag=0x15 sub=0  x=205 y=146 fonth=18
    # [7] IMAGE tag=0x16 sub=0  x=413 y=36
    # [8] DATETIME tag=0x17
    # [9] DATETIME tag=0x18
    # [10] TEXT tag=0x19 sub=0  x=260 y=184 fonth=24

    # Strategy: take EXACT entries, only change x, y, and tag byte.
    # Keep sub, font_ref, colors, everything else identical.

    # Take gauge [0] — clone it for 4 positions, changing only x/y/tag
    gauge_template = get_stock_widget(0)
    print(f"Gauge template: {gauge_template[:20].hex()}")

    # Take text [10] — large font (fonth=24), clone for 4 positions
    text_template = get_stock_widget(10)
    print(f"Text template:  {text_template[:20].hex()}")

    # Take text [5] — medium font (fonth=19)
    text_med_template = get_stock_widget(5)
    print(f"Text med:       {text_med_template[:20].hex()}")

    # Build new widget table with 4 gauges + 4 texts
    new_widgets = bytearray(0x1000 - 0x80)  # zeroed

    widgets = []

    # Gauge 1: top-left (pve CPU)
    w = bytearray(gauge_template)
    w[1] = 0x01  # tag
    struct.pack_into("<H", w, 4, 100)   # x
    struct.pack_into("<H", w, 6, 120)   # y
    widgets.append(bytes(w))

    # Gauge 2: top-right (pve RAM)
    w = bytearray(gauge_template)
    w[1] = 0x02  # tag
    struct.pack_into("<H", w, 4, 250)   # x
    struct.pack_into("<H", w, 6, 120)   # y
    widgets.append(bytes(w))

    # Gauge 3: mid-left (pve3 CPU)
    w = bytearray(gauge_template)
    w[1] = 0x03  # tag
    struct.pack_into("<H", w, 4, 400)   # x
    struct.pack_into("<H", w, 6, 120)   # y
    widgets.append(bytes(w))

    # Gauge 4: mid-right (pve3 RAM)
    w = bytearray(gauge_template)
    w[1] = 0x04  # tag
    struct.pack_into("<H", w, 4, 550)   # x
    struct.pack_into("<H", w, 6, 120)   # y
    widgets.append(bytes(w))

    # Text inside gauges (fonth=24, centered)
    # pve CPU text
    w = bytearray(text_template)
    w[1] = 0x01  # same tag as gauge
    struct.pack_into("<H", w, 4, 110)
    struct.pack_into("<H", w, 6, 150)
    widgets.append(bytes(w))

    # pve RAM text
    w = bytearray(text_template)
    w[1] = 0x02
    struct.pack_into("<H", w, 4, 260)
    struct.pack_into("<H", w, 6, 150)
    widgets.append(bytes(w))

    # pve3 CPU text
    w = bytearray(text_template)
    w[1] = 0x03
    struct.pack_into("<H", w, 4, 410)
    struct.pack_into("<H", w, 6, 150)
    widgets.append(bytes(w))

    # pve3 RAM text
    w = bytearray(text_template)
    w[1] = 0x04
    struct.pack_into("<H", w, 4, 560)
    struct.pack_into("<H", w, 6, 150)
    widgets.append(bytes(w))

    print(f"\nPlacing {len(widgets)} widgets")
    for i, w in enumerate(widgets):
        new_widgets[i*64:(i+1)*64] = w

    # Assemble container
    header = bytearray(container[:0x80])
    new_container = bytearray()
    new_container.extend(header)
    new_container.extend(new_widgets)
    new_container.extend(struct.pack(">I", orig_jpeg_size))

    # Padded JPEG
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    new_container.extend(new_jpeg)
    new_container.extend(b'\x00' * padding)
    new_container.extend(post_jpeg)

    new_total_len = len(new_container)
    struct.pack_into(">I", new_container, 0x58, new_total_len)

    print(f"Container: {new_total_len} bytes")

    chunks, end_packet = chunk_container(bytes(new_container), new_total_len)
    send_upload(device, chunks, end_packet)

    time.sleep(3)

    print("Sending sensor data...")
    test_data = {
        0x01: 17, 0x02: 69, 0x03: 27, 0x04: 56,
    }
    for i in range(5):
        send_sensor_data(device, test_data)
        time.sleep(1)

    print("Done!")


if __name__ == "__main__":
    main()
