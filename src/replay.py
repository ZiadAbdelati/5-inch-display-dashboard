#!/usr/bin/env python3
"""Step 1: Replay captured theme upload to the 5" smart screen.

Replays the first instance of upload[1] (txn 1ae7df64, 50 theme chunks
+ 1 end packet) from the 2ndtest.txt capture to /dev/ttyACM0.
"""

import sys
import time
import serial
from parser import parse_dms_capture

CAPTURE_FILE = "../captures/2ndtest.txt"
TXN_ID = "1ae7df64"
DEVICE = "/dev/ttyACM0"
BAUD = 57600
INTER_PACKET_DELAY = 0.005  # 5ms between packets


def extract_first_upload(writes, txn_id_hex):
    """Extract the first complete upload (chunks 0..N + end) for a txn ID."""
    packets = []
    seen_end = False
    for d, ts, b in writes:
        if d != "Down":
            continue
        if seen_end:
            break
        if b[:5] == b"theme" and b[10:14].hex() == txn_id_hex:
            packets.append(b)
        elif b[:3] == b"end" and b[10:14].hex() == txn_id_hex:
            packets.append(b)
            seen_end = True
    return packets


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE

    print(f"Parsing capture file: {CAPTURE_FILE}")
    writes = parse_dms_capture(CAPTURE_FILE)
    print(f"  Total packets: {len(writes)}")

    packets = extract_first_upload(writes, TXN_ID)
    theme_count = sum(1 for p in packets if p[:5] == b"theme")
    end_count = sum(1 for p in packets if p[:3] == b"end")
    print(f"  Upload txn={TXN_ID}: {theme_count} theme chunks + {end_count} end packet(s)")

    if not packets:
        print("ERROR: No packets found for transaction ID!")
        sys.exit(1)

    # Verify chunk indices are sequential
    theme_chunks = [p for p in packets if p[:5] == b"theme"]
    indices = [p[7] for p in theme_chunks]
    expected = list(range(len(theme_chunks)))
    if indices != expected:
        print(f"WARNING: Chunk indices not sequential! Got: {indices[:10]}...")

    total_bytes = sum(len(p) for p in packets)
    print(f"  Total bytes to send: {total_bytes}")

    print(f"\nOpening {device} at {BAUD} baud...")
    ser = serial.Serial(
        port=device,
        baudrate=BAUD,
        bytesize=serial.EIGHTBITS,
        parity=serial.PARITY_NONE,
        stopbits=serial.STOPBITS_ONE,
        timeout=1,
        write_timeout=5,
    )
    ser.rts = True
    ser.dtr = True
    time.sleep(0.1)  # Let the device settle after open

    print(f"Sending {len(packets)} packets with {INTER_PACKET_DELAY*1000:.0f}ms delay...")
    for i, packet in enumerate(packets):
        if packet[:5] == b"theme":
            label = f"theme[{packet[7]:02d}]"
        else:
            label = "end"
        ser.write(packet)
        ser.flush()
        print(f"  [{i+1}/{len(packets)}] {label} ({len(packet)} bytes)")
        time.sleep(INTER_PACKET_DELAY)

    ser.close()
    print("\nDone! Check the screen.")
    print("Expected: Theme B (the second theme from the capture)")
    print("If nothing changed, see troubleshooting in the brief.")


if __name__ == "__main__":
    main()
