#!/usr/bin/env python3
"""Test: custom widget layout on dark background.

Places gauges and text widgets to approximate the HA Proxmox dashboard,
reusing stock theme resource data for fonts/rendering.
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


def rgb_to_565(r, g, b):
    """Convert RGB888 to RGB565 little-endian."""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


def make_gauge_entry(tag, sub, flags, x, y, diameter, thickness,
                     outline_rgb, active_rgb, inactive_rgb, style=0x0200):
    """Build a 64-byte GAUGE widget entry."""
    entry = bytearray(64)
    entry[0] = 0x92  # GAUGE type
    entry[1] = tag
    entry[2] = sub
    entry[3] = flags
    struct.pack_into("<H", entry, 4, x)
    struct.pack_into("<H", entry, 6, y)
    entry[8] = diameter
    entry[9] = 0x00
    entry[10] = thickness
    entry[11] = 0x01
    struct.pack_into("<H", entry, 12, rgb_to_565(*outline_rgb))
    struct.pack_into("<H", entry, 14, rgb_to_565(*active_rgb))
    struct.pack_into("<H", entry, 16, style)
    struct.pack_into("<H", entry, 18, rgb_to_565(*inactive_rgb))

    # Tick values - copy from a working stock gauge
    # These are from the stock theme's gauge (diam ~52, style 0x0200)
    stock_ticks = [0x0020, 0x000f, 0x000f, 0x000f, 0x000f, 0x000f,
                   0x000f, 0x000f, 0x000f, 0x000f, 0x000f, 0x0008, 0x0009]
    for i, tick in enumerate(stock_ticks):
        struct.pack_into("<H", entry, 20 + i*2, tick)

    return bytes(entry)


def make_dark_jpeg():
    """Dark background JPEG."""
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

    print(f"Loading stock theme {txn} for resources...")
    container, total_len = get_stock_container(txn)

    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
    jpeg_end = JPEG_OFFSET + orig_jpeg_size
    post_jpeg = container[jpeg_end:total_len]

    # Get the original header (0x00-0x7F)
    header = bytearray(container[:0x80])

    # Build custom widget table
    widget_table = bytearray(0x1000 - 0x80)  # zeroed

    # Colors (approximate dashboard)
    green = (139, 195, 74)      # CPU gauge active
    amber = (255, 167, 38)      # RAM gauge active
    dark_gray = (50, 50, 55)    # Inactive arc
    outline = (40, 40, 45)      # Gauge outline (near-invisible)
    white = (255, 255, 255)

    # Layout: 3 cards across top portion
    # Card 1 center: x≈133, Card 2: x≈400, Card 3: x≈667
    # Gauge y center ≈ 200 (middle of card)
    # Left gauge (CPU) offset: -70 from card center
    # Right gauge (RAM) offset: +70 from card center

    gauge_diam = 90
    gauge_thick = 22
    gauge_y = 175  # gauge center y

    widgets = []

    # --- Card 1 (pve) ---
    # CPU gauge - tag 0x01
    widgets.append(make_gauge_entry(
        tag=0x01, sub=5, flags=0x01,
        x=65, y=gauge_y, diameter=gauge_diam, thickness=gauge_thick,
        outline_rgb=outline, active_rgb=green, inactive_rgb=dark_gray))

    # RAM gauge - tag 0x02
    widgets.append(make_gauge_entry(
        tag=0x02, sub=1, flags=0x01,
        x=185, y=gauge_y, diameter=gauge_diam, thickness=gauge_thick,
        outline_rgb=outline, active_rgb=amber, inactive_rgb=dark_gray))

    # --- Card 2 (pve3) ---
    # CPU gauge - tag 0x03
    widgets.append(make_gauge_entry(
        tag=0x03, sub=7, flags=0x01,
        x=335, y=gauge_y, diameter=gauge_diam, thickness=gauge_thick,
        outline_rgb=outline, active_rgb=green, inactive_rgb=dark_gray))

    # RAM gauge - tag 0x04
    widgets.append(make_gauge_entry(
        tag=0x04, sub=3, flags=0x01,
        x=455, y=gauge_y, diameter=gauge_diam, thickness=gauge_thick,
        outline_rgb=outline, active_rgb=amber, inactive_rgb=dark_gray))

    # --- Card 3 (pveunraid) ---
    # CPU gauge - tag 0x05
    widgets.append(make_gauge_entry(
        tag=0x05, sub=2, flags=0x00,
        x=600, y=gauge_y, diameter=gauge_diam, thickness=gauge_thick,
        outline_rgb=outline, active_rgb=green, inactive_rgb=dark_gray))

    # RAM gauge - tag 0x06
    widgets.append(make_gauge_entry(
        tag=0x06, sub=6, flags=0x02,
        x=720, y=gauge_y, diameter=gauge_diam, thickness=gauge_thick,
        outline_rgb=outline, active_rgb=amber, inactive_rgb=dark_gray))

    # --- TEXT widgets for percentage values inside gauges ---
    # Reuse font references from working stock TEXT widgets
    # Stock widget: 93 0b 00 01 50 00 e0 00 11 00 12 00 02 2e 38 ff ff ff
    # fonth=18, fmt bytes = 02 2e, color bytes = 38 ff, bgcolor = ff ff

    # Use the stock font bytes (02 32 for fonth=24 - larger for gauge interior)
    # From stock: 93 19 00 01 04 01 b8 00 31 00 18 00 02 32 fd ff ff ff
    # fonth=24, fmt=0x3202, color=0xfffd (near white)

    def make_text_entry(tag, x, y, width=40, fonth=24, font_ref=0x32,
                        color_rgb=(255, 255, 255)):
        entry = bytearray(64)
        entry[0] = 0x93  # TEXT type
        entry[1] = tag
        entry[2] = 0x00  # sub
        entry[3] = 0x01  # align=center
        struct.pack_into("<H", entry, 4, x)
        struct.pack_into("<H", entry, 6, y)
        entry[8] = width
        entry[9] = 0x00
        entry[10] = fonth
        entry[11] = 0x00
        entry[12] = 0x02  # font set (matching stock theme 1ae7df64)
        entry[13] = font_ref  # font reference within set
        struct.pack_into("<H", entry, 14, rgb_to_565(*color_rgb))
        struct.pack_into("<H", entry, 16, 0xffff)  # bgcolor (transparent/white)
        return bytes(entry)

    # CPU% text inside gauge - centered on gauge x,y
    # tag 0x01 already used for gauge, but TEXT can have same tag
    # Actually tags must be unique per widget for sensor data routing
    # We need separate tags for text values
    # Use tags 0x07-0x0c for the text values
    # (sensor data maps tags to displayed value)

    # Actually wait - the gauge and text can share the same tag!
    # The gauge shows the arc, the text shows the number, both update from same tag.
    # Let me check if that works... The firmware might route sensor data to ALL widgets
    # with the matching tag. Let's try it.

    # Percentage text inside each gauge (share tag with gauge)
    text_y_offset = -5  # slight offset from gauge center for text

    # pve CPU
    widgets.append(make_text_entry(tag=0x01, x=65+gauge_diam//2-15,
                                   y=gauge_y+gauge_diam//2+text_y_offset, width=35))
    # pve RAM
    widgets.append(make_text_entry(tag=0x02, x=185+gauge_diam//2-15,
                                   y=gauge_y+gauge_diam//2+text_y_offset, width=35))
    # pve3 CPU
    widgets.append(make_text_entry(tag=0x03, x=335+gauge_diam//2-15,
                                   y=gauge_y+gauge_diam//2+text_y_offset, width=35))
    # pve3 RAM
    widgets.append(make_text_entry(tag=0x04, x=455+gauge_diam//2-15,
                                   y=gauge_y+gauge_diam//2+text_y_offset, width=35))
    # pveunraid CPU
    widgets.append(make_text_entry(tag=0x05, x=600+gauge_diam//2-15,
                                   y=gauge_y+gauge_diam//2+text_y_offset, width=35))
    # pveunraid RAM
    widgets.append(make_text_entry(tag=0x06, x=720+gauge_diam//2-15,
                                   y=gauge_y+gauge_diam//2+text_y_offset, width=35))

    # Place widgets in table
    for i, w in enumerate(widgets):
        widget_table[i*64:(i+1)*64] = w

    # Assemble container
    new_container = bytearray()
    new_container.extend(header)
    new_container.extend(widget_table)

    # JPEG size field (4 bytes at 0x1000) - keep original size for offset preservation
    new_container.extend(struct.pack(">I", orig_jpeg_size))

    # New JPEG padded to original size
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    new_container.extend(new_jpeg)
    new_container.extend(b'\x00' * padding)

    # Post-JPEG resources (fonts etc)
    new_container.extend(post_jpeg)

    new_total_len = len(new_container)
    struct.pack_into(">I", new_container, 0x58, new_total_len)

    print(f"Container: {new_total_len} bytes ({len(widgets)} widgets)")
    print(f"  Gauges: 6, Text: 6")

    new_container = bytes(new_container)

    print("Sending theme...")
    chunks, end_packet = chunk_container(new_container, new_total_len)
    send_upload(device, chunks, end_packet)

    time.sleep(3)

    print("Sending sensor data...")
    test_data = {
        0x01: 17,   # pve CPU %
        0x02: 69,   # pve RAM %
        0x03: 27,   # pve3 CPU %
        0x04: 56,   # pve3 RAM %
        0x05: 32,   # pveunraid CPU %
        0x06: 59,   # pveunraid RAM %
    }
    for i in range(5):
        send_sensor_data(device, test_data)
        time.sleep(1)

    print("\nDone! Check the screen for gauge layout test.")


if __name__ == "__main__":
    main()
