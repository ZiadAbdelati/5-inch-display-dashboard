#!/usr/bin/env python3
"""Step 2 Variant C: JPEG substitution with JCS_RGB encoding.

Encodes the JPEG with no YCbCr conversion — raw RGB values stored
directly in the JPEG components. This matches the hypothesis that the
firmware's decoder reads components as-is without color conversion.
"""

import sys
import time
import ctypes
import ctypes.util
import serial
import numpy as np
from parser import parse_dms_capture, reconstruct_container
from substitute_jpeg import (
    get_original_container,
    substitute_jpeg,
    set_txn_id,
    chunk_container,
    send_upload,
    DEVICE,
)

# libjpeg-turbo constants
TJPF_RGB = 0
TJSAMP_444 = 0
TJFLAG_NOREALLOC = 1024


def encode_rgb_jpeg(image_data, width, height, quality=85):
    """Encode an RGB image as JPEG using TurboJPEG with TJPF_RGB.

    This uses the TurboJPEG API which internally sets JCS_RGB,
    producing a JPEG where components are R, G, B without YCbCr conversion.
    """
    lib = ctypes.cdll.LoadLibrary("libturbojpeg.so")

    # TurboJPEG API
    lib.tjInitCompress.restype = ctypes.c_void_p
    lib.tjCompress2.argtypes = [
        ctypes.c_void_p,           # handle
        ctypes.POINTER(ctypes.c_ubyte),  # srcBuf
        ctypes.c_int,              # width
        ctypes.c_int,              # pitch
        ctypes.c_int,              # height
        ctypes.c_int,              # pixelFormat
        ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),  # jpegBuf
        ctypes.POINTER(ctypes.c_ulong),  # jpegSize
        ctypes.c_int,              # jpegSubsamp
        ctypes.c_int,              # jpegQual
        ctypes.c_int,              # flags
    ]
    lib.tjCompress2.restype = ctypes.c_int
    lib.tjBufSize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.tjBufSize.restype = ctypes.c_ulong
    lib.tjFree.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    lib.tjDestroy.argtypes = [ctypes.c_void_p]

    handle = lib.tjInitCompress()
    if not handle:
        raise RuntimeError("tjInitCompress failed")

    # Prepare source buffer
    src = np.ascontiguousarray(image_data, dtype=np.uint8)
    src_ptr = src.ctypes.data_as(ctypes.POINTER(ctypes.c_ubyte))

    # Output buffer (let TJ allocate)
    jpeg_buf = ctypes.POINTER(ctypes.c_ubyte)()
    jpeg_size = ctypes.c_ulong(0)

    ret = lib.tjCompress2(
        handle,
        src_ptr,
        width,
        0,  # pitch (0 = width * pixel_size)
        height,
        TJPF_RGB,       # pixel format: RGB
        ctypes.byref(jpeg_buf),
        ctypes.byref(jpeg_size),
        TJSAMP_444,     # 4:4:4 subsampling
        quality,
        0,              # flags
    )

    if ret != 0:
        lib.tjDestroy(handle)
        raise RuntimeError(f"tjCompress2 failed with code {ret}")

    # Copy result
    result = bytes(ctypes.cast(jpeg_buf, ctypes.POINTER(ctypes.c_ubyte * jpeg_size.value)).contents)

    lib.tjFree(jpeg_buf)
    lib.tjDestroy(handle)

    return result


def make_solid_red_rgb_jpeg():
    """Create a solid red 800x480 image encoded as JCS_RGB JPEG."""
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    img[:, :, 0] = 255  # Red channel
    return encode_rgb_jpeg(img, 800, 480, quality=85)


def make_gradient_rgb_jpeg():
    """Create a gradient test pattern to help diagnose color issues."""
    img = np.zeros((480, 800, 3), dtype=np.uint8)
    # Left third: red, middle third: green, right third: blue
    img[:, :267, 0] = 255       # Red
    img[:, 267:534, 1] = 255    # Green
    img[:, 534:, 2] = 255       # Blue
    return encode_rgb_jpeg(img, 800, 480, quality=85)


def main():
    device = sys.argv[1] if len(sys.argv) > 1 else DEVICE
    txn_id_hex = sys.argv[2] if len(sys.argv) > 2 else "deadbeef"
    pattern = sys.argv[3] if len(sys.argv) > 3 else "red"

    txn_bytes = bytes.fromhex(txn_id_hex)
    print(f"Transaction ID: {txn_id_hex}")
    print(f"Pattern: {pattern}")
    print(f"Encoding: JCS_RGB (no YCbCr conversion)")

    print("Parsing capture and reconstructing original container...")
    container, orig_end = get_original_container("../captures/2ndtest.txt", "1ae7df64")
    print(f"  Original container: {len(container)} bytes")

    if pattern == "gradient":
        print("Generating RGB gradient test JPEG...")
        jpeg_data = make_gradient_rgb_jpeg()
    else:
        print("Generating solid red JCS_RGB JPEG...")
        jpeg_data = make_solid_red_rgb_jpeg()
    print(f"  JPEG size: {len(jpeg_data)} bytes")

    # Verify it's a valid JPEG
    assert jpeg_data[:2] == b"\xff\xd8", "Not a valid JPEG!"
    assert jpeg_data[-2:] == b"\xff\xd9", "JPEG missing EOI!"

    print("Substituting JPEG in container...")
    new_container = substitute_jpeg(container, jpeg_data)
    print(f"  New container: {len(new_container)} bytes")

    print("Setting transaction ID...")
    new_container, new_end = set_txn_id(new_container, orig_end, txn_bytes)

    print("Chunking container...")
    chunks = chunk_container(new_container, txn_bytes)
    print(f"  {len(chunks)} chunks")

    send_upload(device, chunks, new_end)
    print(f"\nCheck the screen!")
    if pattern == "gradient":
        print("Expected: Red | Green | Blue vertical stripes")
    else:
        print("Expected: Solid RED background")


if __name__ == "__main__":
    main()
