#!/usr/bin/env python3
"""Proxmox dashboard layout test using stock theme widgets.

Repurposes stock widget entries (keeping at original table indices):
- 8 gauges for CPU/RAM arcs (pve, pve3, pveunraid, unraid)
- 8 texts for percentage values inside gauges
- Remaining widgets hidden off-screen
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


def modify_widget(container, index, tag=None, x=None, y=None,
                   outline_color=None, active_color=None, inactive_color=None,
                   text_color=None):
    """Modify fields on a widget entry at the given table index."""
    off = 0x80 + index * 64
    wtype = container[off]

    if tag is not None:
        container[off + 1] = tag
    if x is not None:
        struct.pack_into("<H", container, off + 4, x)
    if y is not None:
        struct.pack_into("<H", container, off + 6, y)

    if wtype == 0x92:  # GAUGE
        if outline_color is not None:
            struct.pack_into("<H", container, off + 12, outline_color)
        if active_color is not None:
            struct.pack_into("<H", container, off + 14, active_color)
        if inactive_color is not None:
            struct.pack_into("<H", container, off + 18, inactive_color)

    if wtype == 0x93:  # TEXT
        if text_color is not None:
            struct.pack_into("<H", container, off + 14, text_color)


def hide_widget(container, index):
    """Move a widget off-screen."""
    off = 0x80 + index * 64
    if any(container[off:off+64]):
        struct.pack_into("<H", container, off + 4, 900)
        struct.pack_into("<H", container, off + 6, 600)


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    txn = "1ae7df64"

    container, total_len = get_stock_container(txn)
    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]

    c = bytearray(container[:total_len])

    # Replace JPEG with dark background (padded)
    new_jpeg = make_dark_jpeg()
    padding = orig_jpeg_size - len(new_jpeg)
    c[JPEG_OFFSET:JPEG_OFFSET + len(new_jpeg)] = new_jpeg
    c[JPEG_OFFSET + len(new_jpeg):JPEG_OFFSET + orig_jpeg_size] = b'\x00' * padding

    # Colors
    green = rgb565(139, 195, 74)       # CPU gauge active (greenish-yellow)
    amber = rgb565(255, 167, 38)       # RAM gauge active (orange)
    dark_gray = rgb565(55, 55, 60)     # Inactive arc
    dark_outline = rgb565(30, 30, 35)  # Nearly invisible outline
    white = rgb565(255, 255, 255)

    # Available GAUGE indices with sizes:
    # [0]  diam=52  [18] diam=76  [19] diam=76  [26] diam=52
    # [27] diam=52  [31] diam=52  [34] diam=67  [36] diam=52
    # [38] diam=67  [43] diam=90  [45] diam=52  [50] diam=52

    # Available TEXT indices (selected larger fonts):
    # [10] fonth=24  [11] fonth=22  [5] fonth=19  [13] fonth=19
    # [3]  fonth=18  [6] fonth=18  [14] fonth=18  [15] fonth=22
    # [20] fonth=19  [21] fonth=18  ... many more

    # === LAYOUT ===
    # 800x480 screen, 3 cards across top, 1 card bottom
    #
    # Top row (y≈80-240): pve | pve3 | pveunraid
    #   Each card: CPU gauge (left) + RAM gauge (right)
    #   Card centers: x≈133, x≈400, x≈667
    #   CPU gauge x offset: -65, RAM gauge x offset: +65
    #
    # Bottom row (y≈290-430): unraid
    #   CPU gauge + RAM gauge at left side

    # Top row gauges: use the larger gauges
    card_y = 140  # gauge center y for top row

    # Card 1 (pve): CPU=tag 0x01, RAM=tag 0x02
    # Use gauge [43] diam=90 for pve CPU
    modify_widget(c, 43, tag=0x01, x=68, y=card_y,
                  outline_color=dark_outline, active_color=green, inactive_color=dark_gray)
    # Use gauge [18] diam=76 for pve RAM
    modify_widget(c, 18, tag=0x02, x=198, y=card_y,
                  outline_color=dark_outline, active_color=amber, inactive_color=dark_gray)

    # Card 2 (pve3): CPU=tag 0x03, RAM=tag 0x04
    modify_widget(c, 19, tag=0x03, x=335, y=card_y,
                  outline_color=dark_outline, active_color=green, inactive_color=dark_gray)
    modify_widget(c, 38, tag=0x04, x=465, y=card_y,
                  outline_color=dark_outline, active_color=amber, inactive_color=dark_gray)

    # Card 3 (pveunraid): CPU=tag 0x05, RAM=tag 0x06
    modify_widget(c, 34, tag=0x05, x=602, y=card_y,
                  outline_color=dark_outline, active_color=green, inactive_color=dark_gray)
    modify_widget(c, 31, tag=0x06, x=732, y=card_y,
                  outline_color=dark_outline, active_color=amber, inactive_color=dark_gray)

    # Bottom row (unraid): CPU=tag 0x07, RAM=tag 0x08
    bottom_y = 350
    modify_widget(c, 26, tag=0x07, x=80, y=bottom_y,
                  outline_color=dark_outline, active_color=green, inactive_color=dark_gray)
    modify_widget(c, 45, tag=0x08, x=210, y=bottom_y,
                  outline_color=dark_outline, active_color=amber, inactive_color=dark_gray)

    # Text widgets for percentage values inside/near gauges
    # Use various text slots with different font sizes
    # Card 1 texts
    modify_widget(c, 10, tag=0x01, x=90, y=card_y+20, text_color=white)   # pve CPU %
    modify_widget(c, 5,  tag=0x02, x=220, y=card_y+20, text_color=white)  # pve RAM %

    # Card 2 texts
    modify_widget(c, 11, tag=0x03, x=357, y=card_y+20, text_color=white)  # pve3 CPU %
    modify_widget(c, 13, tag=0x04, x=487, y=card_y+20, text_color=white)  # pve3 RAM %

    # Card 3 texts
    modify_widget(c, 3,  tag=0x05, x=624, y=card_y+20, text_color=white)  # pveunraid CPU %
    modify_widget(c, 6,  tag=0x06, x=754, y=card_y+20, text_color=white)  # pveunraid RAM %

    # Bottom texts
    modify_widget(c, 14, tag=0x07, x=102, y=bottom_y+20, text_color=white)  # unraid CPU %
    modify_widget(c, 20, tag=0x08, x=232, y=bottom_y+20, text_color=white)  # unraid RAM %

    # Hide ALL other widgets off-screen
    used_indices = {43, 18, 19, 38, 34, 31, 26, 45,  # gauges
                    10, 5, 11, 13, 3, 6, 14, 20}       # texts
    for i in range(62):
        if i not in used_indices:
            hide_widget(c, i)

    struct.pack_into(">I", c, 0x58, total_len)

    print(f"Dashboard layout: 8 gauges + 8 texts, {total_len} bytes")
    chunks, end_packet = chunk_container(bytes(c), total_len)
    send_upload(device, chunks, end_packet)

    time.sleep(4)

    # Sensor data matching the HA dashboard values
    test_data = {
        0x01: 17,   # pve CPU %
        0x02: 69,   # pve RAM %
        0x03: 27,   # pve3 CPU %
        0x04: 56,   # pve3 RAM %
        0x05: 32,   # pveunraid CPU %
        0x06: 59,   # pveunraid RAM %
        0x07: 48,   # unraid CPU %
        0x08: 70,   # unraid RAM %
    }

    print("Sending sensor data...")
    for i in range(6):
        send_sensor_data(device, test_data)
        time.sleep(1.5)

    print("""
Done! Expected layout:
  Top row: 3 pairs of gauges (green CPU / amber RAM)
    pve(17%/69%)  pve3(27%/56%)  pveunraid(32%/59%)
  Bottom: unraid gauges (48%/70%)
  Text values inside/below each gauge
""")


if __name__ == "__main__":
    main()
