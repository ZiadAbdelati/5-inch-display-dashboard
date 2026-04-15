"""Parse HHD Device Monitoring Studio text export (UTF-16 LE)."""

import re


def parse_dms_capture(path):
    """Parse HHD Device Monitoring Studio text export (UTF-16 LE).

    Returns list of (direction, timestamp, bytes) tuples for each
    IRP_MJ_WRITE packet in the capture.
    """
    with open(path, 'rb') as f:
        text = f.read().decode('utf-16')
    lines = text.split('\n')

    writes = []
    direction = None
    timestamp = None
    buf = bytearray()
    in_write = False

    def flush():
        nonlocal in_write
        if in_write and buf:
            writes.append((direction, timestamp, bytes(buf)))
        in_write = False

    for line in lines:
        if 'Direction' in line and '"Down"' in line:
            flush()
            direction = 'Down'
            buf = bytearray()
            in_write = False
            m = re.search(r'(\d{2}\.\d{2}\.\d{4} \d{2}:\d{2}:\d{2})', line)
            timestamp = m.group(1) if m else None
        elif 'Direction' in line and '"Up"' in line:
            flush()
            direction = 'Up'
            buf = bytearray()
            in_write = False
        elif 'IRP_MJ_WRITE' in line:
            in_write = True
        elif in_write:
            stripped = line.lstrip('\t')
            m = re.match(r'^([0-9a-f]{8})\s+(.+)$', stripped)
            if m:
                rest = m.group(2)
                # Hex part ends at first run of 2+ spaces (ASCII column)
                hex_part = re.split(r'\s{2,}', rest, maxsplit=1)[0]
                for tok in hex_part.split():
                    if len(tok) == 2 and all(c in '0123456789abcdef' for c in tok):
                        buf.append(int(tok, 16))
    flush()
    return writes


def extract_upload(writes, txn_id_hex):
    """Filter to packets for a specific transaction ID."""
    result = []
    for d, ts, b in writes:
        if d != 'Down':
            continue
        if b[:5] == b'theme' and b[10:14].hex() == txn_id_hex:
            result.append(b)
        elif b[:3] == b'end' and b[10:14].hex() == txn_id_hex:
            result.append(b)
    return result


def reconstruct_container(theme_chunks):
    """Concatenate theme chunk payloads (stripping 64-byte headers) to
    recover the original img.dat container bytes.

    Chunk format: 64-byte header (16 meaningful + 48 zero padding)
                + 4096-byte data payload = 4160 bytes total.
    """
    chunks_only = [c for c in theme_chunks if c[:5] == b'theme']
    sorted_chunks = sorted(chunks_only, key=lambda c: c[7])  # by chunk index
    return b''.join(c[64:] for c in sorted_chunks)
