#!/usr/bin/env python3
"""Step 3: Zero-widget theme synthesis.

Builds a container from a captured theme's header (with widget table
zeroed out) and a fresh JPEG background. No visible widget overlays.

The firmware requires widget count > 0 (0 triggers diagnostic mode),
so we keep the original count but zero the widget definitions.
"""

import sys
import struct
from io import BytesIO
from PIL import Image
from parser import parse_dms_capture, reconstruct_container
from substitute_jpeg import crc16_modbus, chunk_container, send_upload, DEVICE

CAPTURE_FILE = "../captures/2ndtest.txt"
ORIG_TXN = "1ae7df64"


def get_header_template(capture_path, txn_id):
    """Extract the first 0x1004 bytes of a captured container as a template."""
    writes = parse_dms_capture(capture_path)
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
    return bytearray(container[:0x1004])


def build_container(jpeg_data, header_template=None):
    """Build a container with zeroed widget table and a JPEG background.

    If header_template is None, builds from a captured theme.
    """
    if header_template is None:
        header_template = get_header_template(CAPTURE_FILE, ORIG_TXN)

    header = bytearray(header_template)

    # Zero out widget table (keeps count non-zero to avoid diagnostic mode)
    header[0x0080:0x1000] = b"\x00" * (0x1000 - 0x0080)

    # Set JPEG size
    struct.pack_into(">I", header, 0x1000, len(jpeg_data))

    # Assemble container
    container = bytearray(bytes(header) + jpeg_data)
    total_len = len(container)

    # Update totalLen in container header
    struct.pack_into(">I", container, 0x58, total_len)

    return bytes(container), total_len


def make_jpeg(image):
    """Encode a PIL Image as JPEG bytes."""
    buf = BytesIO()
    image.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    pattern = sys.argv[2] if len(sys.argv) > 2 else "red"

    colors = {"red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}

    if pattern in colors:
        print(f"Generating solid {pattern} JPEG...")
        img = Image.new("RGB", (800, 480), colors[pattern])
    elif pattern == "gradient":
        print("Generating gradient JPEG...")
        img = Image.new("RGB", (800, 480))
        for x in range(800):
            for y in range(480):
                img.putpixel((x, y), (int(x / 800 * 255), int(y / 480 * 255), 128))
    else:
        # Treat as file path
        print(f"Loading JPEG from {pattern}...")
        img = Image.open(pattern).resize((800, 480))

    jpeg_data = make_jpeg(img)
    print(f"  JPEG: {len(jpeg_data)} bytes")

    print("Building container...")
    container, total_len = build_container(jpeg_data)
    print(f"  Container: {total_len} bytes")

    print("Chunking...")
    chunks, end_packet = chunk_container(container, total_len)
    print(f"  {len(chunks)} chunks")

    send_upload(device, chunks, end_packet)
    print(f"\nDone! Screen should show: {pattern}")


if __name__ == "__main__":
    main()
