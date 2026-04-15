#!/usr/bin/env python3
"""Step 2: JPEG substitution test.

Takes the captured upload[1] theme container, replaces the JPEG at
offset 0x1034 with a new one, recomputes the content hash, re-chunks,
and sends to the screen.

Variant A: Standard Pillow JPEG (solid red 800x480)
"""

import sys
import time
import struct
import serial
from io import BytesIO
from PIL import Image
from parser import parse_dms_capture, reconstruct_container

CAPTURE_FILE = "../captures/2ndtest.txt"
ORIG_TXN = "1ae7df64"
DEVICE = "/dev/ttyACM0"
BAUD = 57600
INTER_PACKET_DELAY = 0.005
JPEG_OFFSET = 0x1004   # JPEG SOI in img.dat container
CHUNK_DATA = 4096       # bytes of container data per chunk
CHUNK_HEADER = 64       # 16 meaningful bytes + 48 zero padding
CHUNK_TOTAL = 4160      # 64-byte header + 4096 data


def crc16_modbus(data):
    """CRC-16/MODBUS: poly 0xA001, init 0xFFFF."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc & 0xFFFF


def get_original_container(capture_path, txn_id):
    """Parse capture and reconstruct the first upload's container."""
    writes = parse_dms_capture(capture_path)
    packets = []
    for d, ts, b in writes:
        if d != "Down":
            continue
        if b[:5] == b"theme" and b[10:14].hex() == txn_id:
            packets.append(b)
        elif b[:3] == b"end" and b[10:14].hex() == txn_id:
            packets.append(b)
            break
    theme_chunks = [p for p in packets if p[:5] == b"theme"]
    end_packet = [p for p in packets if p[:3] == b"end"][0]
    container = reconstruct_container(theme_chunks)

    # Extract original totalLen from chunk header
    first_chunk = sorted(theme_chunks, key=lambda c: c[7])[0]
    total_len = int.from_bytes(first_chunk[8:12], "big")
    return container, end_packet, total_len


def make_test_jpeg():
    """Variant A: solid red 800x480 standard Pillow JPEG."""
    img = Image.new("RGB", (800, 480), (255, 0, 0))
    buf = BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def substitute_jpeg(container, new_jpeg, total_len):
    """Replace the JPEG in the container, update size field, adjust totalLen."""
    # Find original JPEG EOI
    orig_eoi = container.find(b"\xff\xd9", JPEG_OFFSET)
    if orig_eoi == -1:
        raise ValueError("No JPEG EOI found in original container")
    orig_jpeg_len = orig_eoi + 2 - JPEG_OFFSET

    pre_jpeg = container[:JPEG_OFFSET]
    post_jpeg = container[orig_eoi + 2 : total_len]  # data after JPEG within totalLen

    # Build new container: header + new JPEG + post-JPEG data
    new_container = bytearray(pre_jpeg)
    new_container.extend(new_jpeg)
    new_container.extend(post_jpeg)
    new_total_len = len(new_container)

    # Update the JPEG size field at offset 0x1000 (big-endian u32)
    struct.pack_into(">I", new_container, 0x1000, len(new_jpeg))
    print(f"  Updated JPEG size field @0x1000 to {len(new_jpeg)}")
    print(f"  Original JPEG: {orig_jpeg_len} bytes, new: {len(new_jpeg)} bytes")
    print(f"  Original totalLen: {total_len}, new: {new_total_len}")

    return bytes(new_container), new_total_len


def compute_content_hash(container, total_len):
    """Compute the content hash: totalLen (BE32) + CRC-16/MODBUS (BE16).

    Returns the 6-byte header field [8:14] and the 4-byte 'txn ID' [10:14].
    """
    crc = crc16_modbus(container[:total_len])
    header_8_14 = struct.pack(">IH", total_len, crc)
    txn_bytes = header_8_14[2:]  # bytes [10:14] = totalLen_low16 + CRC
    print(f"  totalLen: {total_len} (0x{total_len:x})")
    print(f"  CRC-16/MODBUS: 0x{crc:04x}")
    print(f"  Content hash (header[8:14]): {header_8_14.hex()}")
    print(f"  TXN ID (header[10:14]): {txn_bytes.hex()}")
    return header_8_14, txn_bytes


def chunk_container(container, total_len, type_byte=0x03):
    """Split container into 4160-byte theme chunks with proper headers.

    Chunk format:
      [0:5]   = "theme" magic
      [5]     = 0x00
      [6:8]   = chunk index (big-endian u16)
      [8:12]  = totalLen (big-endian u32)
      [12:14] = totalCrc (big-endian u16, CRC-16/MODBUS)
      [14:16] = 0x0000
      [16:64] = 0x00 * 48 (padding)
      [64:4160] = 4096 bytes of container data (zero-padded)
    """
    header_8_14, txn_bytes = compute_content_hash(container, total_len)

    chunks = []
    offset = 0
    chunk_idx = 0
    while offset < total_len:
        payload = container[offset : offset + CHUNK_DATA]
        if len(payload) < CHUNK_DATA:
            payload = payload + b"\x00" * (CHUNK_DATA - len(payload))

        header = bytearray(CHUNK_HEADER)
        header[0:5] = b"theme"
        header[5] = 0x00
        header[6] = (chunk_idx >> 8) & 0xFF
        header[7] = chunk_idx & 0xFF
        header[8:14] = header_8_14
        header[14:16] = b"\x00\x00"
        # bytes 16-63 remain zero

        chunks.append(bytes(header) + payload)
        offset += CHUNK_DATA
        chunk_idx += 1

    # Build end packet with matching content hash
    end_packet = bytearray(64)
    end_packet[0:3] = b"end"
    end_packet[8:14] = header_8_14  # same totalLen + totalCrc as theme chunks

    return chunks, end_packet


def wait_for_device(device, timeout=10):
    """Wait for a serial device to appear (handles USB re-enumeration)."""
    import os
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if os.path.exists(device):
            time.sleep(0.2)  # brief settle time
            return True
        time.sleep(0.3)
    return False


def send_upload(device, chunks, end_packet, delay=INTER_PACKET_DELAY):
    """Send chunks + end packet to device."""
    if not wait_for_device(device):
        raise IOError(f"{device} not found after waiting")
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
    time.sleep(0.1)

    total = len(chunks) + 1
    print(f"Sending {total} packets ({len(chunks)} chunks + 1 end)...")
    for i, chunk in enumerate(chunks):
        ser.write(chunk)
        ser.flush()
        if i % 10 == 0 or i == len(chunks) - 1:
            print(f"  [{i+1}/{total}] theme[{chunk[7]:02d}] ({len(chunk)} bytes)")
        time.sleep(delay)

    ser.write(end_packet)
    ser.flush()
    print(f"  [{total}/{total}] end ({len(end_packet)} bytes)")

    ser.close()
    print("Done!")


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE

    print("Parsing capture and reconstructing original container...")
    container, orig_end, orig_total_len = get_original_container(CAPTURE_FILE, ORIG_TXN)
    print(f"  Original container: {len(container)} bytes, totalLen: {orig_total_len}")

    print("Generating test JPEG (solid red 800x480)...")
    jpeg_data = make_test_jpeg()
    print(f"  JPEG size: {len(jpeg_data)} bytes")

    print("Substituting JPEG in container...")
    new_container, new_total_len = substitute_jpeg(container, jpeg_data, orig_total_len)

    print("Computing content hash and chunking...")
    chunks, end_packet = chunk_container(new_container, new_total_len)
    print(f"  {len(chunks)} chunks")

    send_upload(device, chunks, end_packet)
    print(f"\nCheck the screen! Expected: solid RED background with theme widgets.")


if __name__ == "__main__":
    main()
