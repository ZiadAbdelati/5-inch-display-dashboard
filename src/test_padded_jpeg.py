#!/usr/bin/env python3
"""Test: swap JPEG but pad to original size to keep post-JPEG offsets intact.

Hypothesis: firmware uses absolute offsets to find font/resource data
after the JPEG. Changing JPEG size shifts those resources and breaks rendering.
Fix: pad new JPEG with zeros to match original JPEG size.
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
    img = Image.new("RGB", (800, 480), (20, 20, 30))
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
    txn = "1ae7df64"  # The theme from replay.py (51 widgets, 94KB post-JPEG)

    print(f"Loading stock theme {txn}...")
    container, total_len = get_stock_container(txn)
    print(f"  Container: {len(container)} bytes, totalLen: {total_len}")

    orig_jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
    jpeg_end = JPEG_OFFSET + orig_jpeg_size
    post_jpeg = container[jpeg_end:total_len]
    print(f"  Original JPEG: {orig_jpeg_size} bytes (0x{JPEG_OFFSET:x} to 0x{jpeg_end:x})")
    print(f"  Post-JPEG: {len(post_jpeg)} bytes (0x{jpeg_end:x} to 0x{total_len:x})")

    new_jpeg = make_dark_jpeg()
    print(f"  New JPEG: {len(new_jpeg)} bytes")

    if len(new_jpeg) > orig_jpeg_size:
        print(f"  ERROR: New JPEG ({len(new_jpeg)}) larger than original ({orig_jpeg_size})!")
        sys.exit(1)

    # Pad new JPEG with zeros to match original size
    padding = orig_jpeg_size - len(new_jpeg)
    padded_jpeg = new_jpeg + b'\x00' * padding
    print(f"  Padded JPEG: {len(padded_jpeg)} bytes ({padding} bytes padding)")

    # Build container: original header + padded JPEG + original post-JPEG
    new_container = bytearray(container[:JPEG_OFFSET])
    # JPEG size field still shows original size (so firmware reads past our JPEG into padding)
    # Actually - let's keep the JPEG size as the ORIGINAL size, since the firmware
    # needs to know where post-JPEG starts (which is at the same offset)
    # The padding bytes after the real JPEG end will be ignored
    new_container.extend(padded_jpeg)
    new_container.extend(post_jpeg)
    new_total_len = len(new_container)

    # totalLen should match the original since we padded to same size
    struct.pack_into(">I", new_container, 0x58, new_total_len)
    print(f"  New container: {new_total_len} bytes (original: {total_len})")
    print(f"  totalLen match: {new_total_len == total_len}")

    new_container = bytes(new_container)

    print("\nChunking and sending...")
    chunks, end_packet = chunk_container(new_container, new_total_len)
    send_upload(device, chunks, end_packet)

    print("\nWaiting 3 seconds...")
    time.sleep(3)

    print("Sending sensor data...")
    test_data = {
        0x01: 45, 0x03: 67, 0x05: 55, 0x07: 82,
        0x0a: 8192, 0x0b: 4096, 0x0c: 67, 0x0d: 38,
        0x11: 55, 0x12: 150, 0x13: 300, 0x14: 50,
    }
    for i in range(3):
        send_sensor_data(device, test_data)
        time.sleep(1)

    print("\nDone! If widgets render properly, the offset hypothesis is confirmed.")


if __name__ == "__main__":
    main()
