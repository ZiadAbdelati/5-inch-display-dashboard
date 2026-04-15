#!/usr/bin/env python3
"""Test: copy stock theme widget table into a new container with dark JPEG.

If stock widgets render correctly with our container building code,
the problem is in our custom widget field values, not the container structure.
"""

import struct
import sys
import time
from io import BytesIO
from PIL import Image
from parser import parse_dms_capture, reconstruct_container
from substitute_jpeg import crc16_modbus, chunk_container, send_upload

CAPTURE_FILE = "../captures/2ndtest.txt"
DEVICE = "/dev/ttyACM0"


def get_stock_container(txn_id):
    """Get a complete stock container from captures."""
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
    return reconstruct_container(chunks)


def make_dark_jpeg():
    """Dark gray 800x480 JPEG."""
    img = Image.new("RGB", (800, 480), (20, 20, 30))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85, subsampling=0)
    return buf.getvalue()


def build_test_container(stock_container, jpeg_data):
    """Build container using stock header+widgets but with our JPEG."""
    # Copy full header including widget table from stock theme
    header = bytearray(stock_container[:0x1004])

    # Set new JPEG size
    struct.pack_into(">I", header, 0x1000, len(jpeg_data))

    # Build container: header + JPEG (no post-JPEG data)
    container = bytearray(bytes(header) + jpeg_data)
    total_len = len(container)

    # Update totalLen at 0x58
    struct.pack_into(">I", container, 0x58, total_len)

    return bytes(container), total_len


def build_0x66(tag_values):
    """Build a 0x66 sensor data packet."""
    buf = bytearray(b'\x66\x00\x4d\x01\x1a\x04\x0e\x10\x00\x00\x01\x64')
    for tag in range(0x01, 0x16):
        val = tag_values.get(tag, 0)
        buf += bytes([tag, (val >> 8) & 0xFF, val & 0xFF])
    crc = crc16_modbus(bytes(buf))
    buf += struct.pack('>H', crc)
    return bytes(buf)


def send_sensor_data(device, tag_values):
    """Send a sensor data packet."""
    import serial
    from substitute_jpeg import wait_for_device

    if not wait_for_device(device):
        print(f"Device {device} not found")
        return

    packet = build_0x66(tag_values)
    ser = serial.Serial(port=device, baudrate=57600, timeout=1, write_timeout=5)
    ser.rts = True
    ser.dtr = True
    time.sleep(0.05)
    ser.write(packet)
    ser.flush()
    ser.close()
    print(f"Sent sensor packet ({len(packet)} bytes)")


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE

    # Use the first theme from 2ndtest.txt (has 16 widgets - gauges + text)
    # TXN d6b9a480 has gauges for tags 0x01-0x0d, text for 0x0b/0x0c, datetime
    txn = "d6b9a480"

    print(f"Loading stock theme {txn}...")
    stock = get_stock_container(txn)
    print(f"  Stock container: {len(stock)} bytes")

    # Show what widgets we have
    widget_table = stock[0x0080:0x1000]
    active = 0
    for i in range(len(widget_table) // 64):
        entry = widget_table[i*64:(i+1)*64]
        if any(b != 0 for b in entry):
            wtype = entry[0]
            tag = entry[1]
            types = {0x92: "GAUGE", 0x93: "TEXT", 0x8b: "BAR", 0x8e: "DATETIME", 0x84: "IMAGE"}
            x = struct.unpack_from("<H", entry, 4)[0]
            y = struct.unpack_from("<H", entry, 6)[0]
            print(f"  [{i:2d}] {types.get(wtype, '???')}(0x{wtype:02x}) tag=0x{tag:02x} @ ({x},{y})")
            active += 1
    print(f"  {active} active widgets")

    print("\nGenerating dark JPEG background...")
    jpeg = make_dark_jpeg()
    print(f"  JPEG: {len(jpeg)} bytes")

    print("Building test container with stock widgets...")
    container, total_len = build_test_container(stock, jpeg)
    print(f"  Container: {total_len} bytes")

    print("Chunking and sending...")
    chunks, end_packet = chunk_container(container, total_len)
    send_upload(device, chunks, end_packet)

    print("\nWaiting 3 seconds for theme to load...")
    time.sleep(3)

    # Send some sensor data to see if widgets update
    print("Sending sensor data...")
    test_data = {
        0x01: 45,   # CPU temp
        0x02: 30,   # CPU temp 2
        0x03: 67,   # CPU usage %
        0x04: 1200, # CPU fan RPM
        0x05: 55,   # GPU temp
        0x06: 40,   # GPU temp 2
        0x07: 82,   # GPU usage %
        0x08: 900,  # GPU fan RPM
        0x09: 50,   # ?
        0x0a: 8192, # RAM used MB
        0x0b: 4096, # RAM free MB
        0x0c: 67,   # RAM usage %
        0x0d: 38,   # Disk temp
        0x0e: 0,    #
        0x0f: 0,    #
        0x10: 25,   # Volume?
        0x11: 55,   # Disk usage %
        0x12: 150,  # UP kb/s
        0x13: 300,  # DOWN kb/s
        0x14: 50,   # VOL %
        0x15: 0,
    }

    for i in range(3):
        send_sensor_data(device, test_data)
        time.sleep(1)

    print("\nDone! Check the screen — stock widgets should render on dark background.")
    print("If this works, the issue was in our custom widget entry construction.")
    print("If not, the issue is in container building or missing post-JPEG data.")


if __name__ == "__main__":
    main()
