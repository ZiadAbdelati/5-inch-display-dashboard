#!/usr/bin/env python3
"""Test: send exact stock container through our chunk_container + send_upload.

If this ALSO garbles, the issue is in our chunking/sending code.
If this works, the issue is that swapping JPEG size shifts post-JPEG offsets.
"""

import struct
import sys
import time
from parser import parse_dms_capture, reconstruct_container
from substitute_jpeg import crc16_modbus, chunk_container, send_upload, DEVICE

CAPTURE_FILE = "../captures/2ndtest.txt"


def get_stock_container_and_packets(txn_id):
    """Get both the reconstructed container AND the original packets."""
    writes = parse_dms_capture(CAPTURE_FILE)
    packets = []
    for d, ts, b in writes:
        if d != "Down":
            continue
        if b[:5] == b"theme" and b[10:14].hex() == txn_id:
            packets.append(b)
        elif b[:3] == b"end" and b[10:14].hex() == txn_id:
            packets.append(b)
            break
    theme_chunks = sorted([p for p in packets if p[:5] == b"theme"], key=lambda p: p[7])
    end_packet = [p for p in packets if p[:3] == b"end"][0]
    container = reconstruct_container(theme_chunks)
    total_len = struct.unpack_from(">I", theme_chunks[0], 8)[0]
    return container, total_len, theme_chunks, end_packet


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
    print(f"Sent sensor packet")


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    txn = "d6b9a480"

    print(f"Loading stock theme {txn}...")
    container, total_len, orig_chunks, orig_end = get_stock_container_and_packets(txn)
    print(f"  Container: {len(container)} bytes, totalLen: {total_len}")

    # Verify our CRC matches the original
    our_crc = crc16_modbus(container[:total_len])
    orig_crc = struct.unpack_from(">H", orig_chunks[0], 12)[0]
    print(f"  Original CRC: 0x{orig_crc:04x}")
    print(f"  Our CRC:      0x{our_crc:04x}")
    print(f"  CRC match: {our_crc == orig_crc}")

    # Compare our chunks with original chunks
    print("\nRe-chunking with our code...")
    our_chunks, our_end = chunk_container(container, total_len)
    print(f"  Original: {len(orig_chunks)} chunks")
    print(f"  Ours: {len(our_chunks)} chunks")

    # Compare chunk by chunk
    mismatches = 0
    for i in range(min(len(orig_chunks), len(our_chunks))):
        if orig_chunks[i] != our_chunks[i]:
            mismatches += 1
            # Find first difference
            for j in range(min(len(orig_chunks[i]), len(our_chunks[i]))):
                if orig_chunks[i][j] != our_chunks[i][j]:
                    print(f"  Chunk {i}: first diff at byte {j}: orig=0x{orig_chunks[i][j]:02x} ours=0x{our_chunks[i][j]:02x}")
                    # Show surrounding context
                    start = max(0, j-4)
                    end = min(len(orig_chunks[i]), j+4)
                    print(f"    orig[{start}:{end}]: {orig_chunks[i][start:end].hex()}")
                    print(f"    ours[{start}:{end}]: {our_chunks[i][start:end].hex()}")
                    break

    if mismatches == 0:
        print("  All chunks MATCH perfectly!")
    else:
        print(f"  {mismatches} chunk mismatches!")

    # Compare end packets
    if orig_end == our_end:
        print("  End packets MATCH")
    else:
        print("  End packets DIFFER!")
        for j in range(min(len(orig_end), len(our_end))):
            if orig_end[j] != our_end[j]:
                print(f"    First diff at byte {j}: orig=0x{orig_end[j]:02x} ours=0x{our_end[j]:02x}")
                break

    # Method 1: Send via our chunk_container
    print("\n--- Method 1: Our chunk_container ---")
    send_upload(device, our_chunks, bytes(our_end))

    time.sleep(3)
    print("Sending sensor data...")
    test_data = {0x01: 45, 0x03: 67, 0x05: 55, 0x07: 82, 0x0c: 67, 0x0d: 38}
    for i in range(3):
        send_sensor_data(device, test_data)
        time.sleep(1)

    print("\n--- Check screen now (our chunking). Press Enter to try original packets... ---")
    input()

    # Method 2: Send original captured packets byte-for-byte
    print("--- Method 2: Original captured packets ---")
    send_upload(device, orig_chunks, orig_end)

    time.sleep(3)
    print("Sending sensor data...")
    for i in range(3):
        send_sensor_data(device, test_data)
        time.sleep(1)

    print("\nDone! Compare: did Method 2 (original packets) render correctly?")


if __name__ == "__main__":
    main()
