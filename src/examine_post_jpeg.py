#!/usr/bin/env python3
"""Examine post-JPEG data in stock theme containers."""

import struct
from parser import parse_dms_capture, reconstruct_container

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


def main():
    for txn in ["d6b9a480", "1ae7df64"]:
        container, total_len = get_stock_container(txn)
        jpeg_size = struct.unpack_from(">I", container, 0x1000)[0]
        jpeg_start = 0x1004
        jpeg_end = jpeg_start + jpeg_size
        post_jpeg_start = jpeg_end
        post_jpeg_size = total_len - post_jpeg_start

        print(f"Theme {txn}:")
        print(f"  totalLen: {total_len}")
        print(f"  JPEG: offset 0x{jpeg_start:x}, size {jpeg_size} bytes")
        print(f"  JPEG end: 0x{jpeg_end:x}")
        print(f"  Post-JPEG: offset 0x{post_jpeg_start:x}, size {post_jpeg_size} bytes")

        if post_jpeg_size > 0:
            post = container[post_jpeg_start:total_len]
            # Show first 256 bytes
            print(f"  Post-JPEG first 256 bytes:")
            for row in range(16):
                start = row * 16
                if start >= len(post):
                    break
                end = min(start + 16, len(post))
                hex_str = " ".join(f"{post[start+i]:02x}" for i in range(end - start))
                ascii_str = "".join(chr(post[start+i]) if 32 <= post[start+i] < 127 else "." for i in range(end - start))
                print(f"    {post_jpeg_start + start:06x}: {hex_str:<48s}  {ascii_str}")

            # Look for patterns - count non-zero bytes per 1K block
            print(f"\n  Post-JPEG density (non-zero bytes per 1K block):")
            for block in range(0, post_jpeg_size, 1024):
                chunk = post[block:block+1024]
                nonzero = sum(1 for b in chunk if b != 0)
                bar = "#" * (nonzero * 50 // 1024)
                print(f"    0x{post_jpeg_start + block:06x}: {nonzero:4d}/1024 {bar}")

            # Check if it's more widget data or resource data
            # Look for widget type bytes at 64-byte boundaries
            print(f"\n  Checking for widget-like entries at 64-byte boundaries:")
            widget_types = {0x84, 0x8b, 0x8e, 0x92, 0x93}
            for i in range(0, min(post_jpeg_size, 4096), 64):
                if post[i] in widget_types:
                    hex_preview = " ".join(f"{post[i+j]:02x}" for j in range(min(18, len(post)-i)))
                    print(f"    @0x{post_jpeg_start + i:06x}: {hex_preview}")

        print()


if __name__ == "__main__":
    main()
