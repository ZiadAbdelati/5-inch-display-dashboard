#!/usr/bin/env python3
"""Dump widget table entries from stock theme captures.

Extracts widget table (0x0080-0x0FFF) from each captured theme and
prints non-zero entries with hex + field interpretation.
"""

import struct
from parser import parse_dms_capture, reconstruct_container

CAPTURE_FILE = "../captures/2ndtest.txt"

# Known widget types from vendor software
WIDGET_TYPES = {
    0x84: "IMAGE",
    0x8b: "BAR",
    0x8e: "DATETIME",
    0x92: "GAUGE",
    0x93: "TEXT",
}


def extract_all_themes(capture_path):
    """Find all theme uploads in a capture file."""
    writes = parse_dms_capture(capture_path)

    # Find all unique transaction IDs
    txn_ids = {}
    for d, ts, b in writes:
        if d != "Down" or len(b) < 14:
            continue
        if b[:5] == b"theme":
            txn = b[10:14].hex()
            if txn not in txn_ids:
                txn_ids[txn] = []
            txn_ids[txn].append(b)

    themes = {}
    for txn, packets in txn_ids.items():
        chunks = sorted(packets, key=lambda c: c[7])
        container = reconstruct_container(chunks)
        themes[txn] = container

    return themes


def dump_widget_entry(data, index, offset):
    """Print a single 64-byte widget table entry."""
    if all(b == 0 for b in data):
        return False

    wtype = data[0]
    tag = data[1]
    type_name = WIDGET_TYPES.get(wtype, f"0x{wtype:02x}")

    print(f"\n  Widget [{index}] @ 0x{offset:04x} — type={type_name}(0x{wtype:02x}) tag=0x{tag:02x}")

    # Print raw hex in rows of 16
    for row in range(4):
        start = row * 16
        hex_str = " ".join(f"{data[start+i]:02x}" for i in range(16))
        ascii_str = "".join(chr(data[start+i]) if 32 <= data[start+i] < 127 else "." for i in range(16))
        print(f"    {start:04x}: {hex_str}  {ascii_str}")

    # Type-specific field decoding
    if wtype == 0x93:  # TEXT
        sub = data[2]
        align = data[3]
        x = struct.unpack_from("<H", data, 4)[0]
        y = struct.unpack_from("<H", data, 6)[0]
        w = data[8]
        h = data[10]  # font height
        fmt = struct.unpack_from("<H", data, 12)[0]
        color = struct.unpack_from("<H", data, 14)[0]
        bgcolor = struct.unpack_from("<H", data, 16)[0]
        print(f"    TEXT: sub={sub} align={align} x={x} y={y} w={w} fonth={h} fmt=0x{fmt:04x} color=0x{color:04x} bgcolor=0x{bgcolor:04x}")
        # Decode RGB565 color
        r = ((color >> 11) & 0x1F) * 255 // 31
        g = ((color >> 5) & 0x3F) * 255 // 63
        b = (color & 0x1F) * 255 // 31
        print(f"    color RGB: ({r}, {g}, {b})")

    elif wtype == 0x92:  # GAUGE
        sub = data[2]
        flags = data[3]
        x = struct.unpack_from("<H", data, 4)[0]
        y = struct.unpack_from("<H", data, 6)[0]
        diameter = data[8]
        thickness = data[10]
        outline_color = struct.unpack_from("<H", data, 12)[0]
        active_color = struct.unpack_from("<H", data, 14)[0]
        style = struct.unpack_from("<H", data, 16)[0]
        inactive_color = struct.unpack_from("<H", data, 18)[0]
        print(f"    GAUGE: sub={sub} flags=0x{flags:02x} x={x} y={y} diam={diameter} thick={thickness}")
        print(f"    outline=0x{outline_color:04x} active=0x{active_color:04x} style=0x{style:04x} inactive=0x{inactive_color:04x}")
        # Tick values at offset 20-45
        ticks = []
        for i in range(13):
            tick = struct.unpack_from("<H", data, 20 + i*2)[0]
            ticks.append(tick)
        print(f"    ticks: {ticks}")

    elif wtype == 0x8b:  # BAR
        sub = data[2]
        orient = data[3]
        x = struct.unpack_from("<H", data, 4)[0]
        y = struct.unpack_from("<H", data, 6)[0]
        w = data[8]
        h = data[10]
        color = struct.unpack_from("<H", data, 12)[0]
        bgcolor = struct.unpack_from("<H", data, 14)[0]
        print(f"    BAR: sub={sub} orient={orient} x={x} y={y} w={w} h={h} color=0x{color:04x} bgcolor=0x{bgcolor:04x}")

    elif wtype == 0x8e:  # DATETIME
        sub = data[2]
        fmt = data[3]
        x = struct.unpack_from("<H", data, 4)[0]
        y = struct.unpack_from("<H", data, 6)[0]
        print(f"    DATETIME: sub={sub} fmt={fmt} x={x} y={y}")

    elif wtype == 0x84:  # IMAGE
        sub = data[2]
        x = struct.unpack_from("<H", data, 4)[0]
        y = struct.unpack_from("<H", data, 6)[0]
        w = struct.unpack_from("<H", data, 8)[0]
        h = struct.unpack_from("<H", data, 10)[0]
        print(f"    IMAGE: sub={sub} x={x} y={y} w={w} h={h}")

    return True


def main():
    import sys
    capture = sys.argv[1] if len(sys.argv) > 1 else CAPTURE_FILE

    print(f"Parsing {capture}...")
    themes = extract_all_themes(capture)
    print(f"Found {len(themes)} theme uploads\n")

    for txn, container in themes.items():
        total_len_field = struct.unpack_from(">I", container, 0x58)[0] if len(container) > 0x5C else 0
        widget_count = struct.unpack_from("<H", container, 0)[0] if len(container) > 2 else 0
        jpeg_size = struct.unpack_from(">I", container, 0x1000)[0] if len(container) > 0x1004 else 0

        print(f"{'='*70}")
        print(f"Theme TXN: {txn}")
        print(f"  Container size: {len(container)} bytes")
        print(f"  totalLen field @0x58: {total_len_field}")
        print(f"  Widget count @0x00: {widget_count} (0x{widget_count:04x})")
        print(f"  JPEG size @0x1000: {jpeg_size}")

        # Dump header bytes 0x00-0x7F
        print(f"\n  Header (0x00-0x7F):")
        for row in range(8):
            start = row * 16
            hex_str = " ".join(f"{container[start+i]:02x}" for i in range(16))
            print(f"    {start:04x}: {hex_str}")

        # Dump widget table
        print(f"\n  Widget table (0x0080-0x0FFF):")
        widget_table = container[0x0080:0x1000]
        active_count = 0
        for i in range(len(widget_table) // 64):
            entry = widget_table[i*64:(i+1)*64]
            if dump_widget_entry(entry, i, 0x0080 + i*64):
                active_count += 1

        print(f"\n  Active widget entries: {active_count}")
        print()

    # Also check the second capture file
    capture2 = "../captures/balls.txt"
    try:
        print(f"\n{'#'*70}")
        print(f"Parsing {capture2}...")
        themes2 = extract_all_themes(capture2)
        print(f"Found {len(themes2)} theme uploads\n")
        for txn, container in themes2.items():
            widget_count = struct.unpack_from("<H", container, 0)[0]
            print(f"Theme TXN: {txn} — widget count: {widget_count}")
            widget_table = container[0x0080:0x1000]
            active_count = 0
            for i in range(len(widget_table) // 64):
                entry = widget_table[i*64:(i+1)*64]
                if dump_widget_entry(entry, i, 0x0080 + i*64):
                    active_count += 1
            print(f"  Active widget entries: {active_count}\n")
    except Exception as e:
        print(f"Could not parse {capture2}: {e}")


if __name__ == "__main__":
    main()
