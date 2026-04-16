#!/usr/bin/env python3
"""Smart screen web dashboard daemon.

Screenshots any URL at 800x480 and pushes it to the USB smart screen
on a configurable interval. Works with any web dashboard: Home Assistant,
Grafana, or any other URL.

Usage:
    screen_daemon.py <url> [options]

Examples:
    screen_daemon.py http://homeassistant.local:8123/dashboard
    screen_daemon.py http://localhost:3000/d/my-grafana-board --interval 10
    screen_daemon.py http://example.com/status --device /dev/ttyACM1
    screen_daemon.py screenshot.png --interval 0   # one-shot from file

    # Home Assistant (headless, long-lived token):
    #   Profile -> Security -> Create Long-Lived Access Token
    screen_daemon.py https://ha.example.com/dashboard --ha-token $HA_TOKEN
    screen_daemon.py https://ha.example.com/dashboard --ha-token-file ~/.ha_token

    # Other authenticated dashboards (cookie-based):
    #   Capture a browser state once with capture_auth.py, then:
    screen_daemon.py https://example.com/dashboard --auth-state state.json
"""

import argparse
import json
import os
import signal
import struct
import sys
import time
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image

from substitute_jpeg import crc16_modbus, chunk_container, send_upload

WIDTH = 800
HEIGHT = 480
HEADER_SIZE = 0x1004


def get_header_template():
    """Load or build the container header template.

    Uses a cached binary template if available, otherwise extracts
    from a captured theme.
    """
    cache = Path(__file__).parent / "header_template.bin"
    if cache.exists():
        data = bytearray(cache.read_bytes())
    else:
        # Build from captured data
        from parser import parse_dms_capture, reconstruct_container

        capture = Path(__file__).parent / "../captures/2ndtest.txt"
        writes = parse_dms_capture(str(capture))
        packets = []
        for d, ts, b in writes:
            if d != "Down":
                continue
            if b[:5] == b"theme" and b[10:14].hex() == "1ae7df64":
                packets.append(b)
            elif b[:3] == b"end" and b[10:14].hex() == "1ae7df64":
                break
        chunks = sorted(
            [p for p in packets if p[:5] == b"theme"], key=lambda p: p[7]
        )
        container = reconstruct_container(chunks)
        data = bytearray(container[:HEADER_SIZE])

        # Zero out widget table for clean display
        data[0x0080:0x1000] = b"\x00" * (0x1000 - 0x0080)

        # Cache for future runs
        cache.write_bytes(bytes(data))
        print(f"Cached header template to {cache}")

    return data


def inject_ha_long_lived_token(page, token, url):
    """Inject a Home Assistant long-lived access token into localStorage.

    HA's frontend reads hassTokens from localStorage on startup. We
    synthesize a tokens object using the LLT as access_token with an
    "expires" timestamp far in the future, so the frontend uses it
    directly without attempting an OAuth refresh.
    """
    parts = urlparse(url)
    origin = f"{parts.scheme}://{parts.netloc}"
    # Expires: year 2099. Long-lived tokens are valid for 10 years; this
    # value just needs to be comfortably ahead so the frontend trusts it.
    tokens = {
        "access_token": token,
        "token_type": "Bearer",
        # HA frontend checks `expires` (absolute ms) before `expires_in`.
        "expires_in": 315360000,
        "expires": 4102444800000,
        "hassUrl": origin,
        "clientId": f"{origin}/",
        "ha_auth_provider": "homeassistant",
    }
    page.goto(origin, wait_until="commit", timeout=10000)
    page.evaluate(
        "(t) => localStorage.setItem('hassTokens', JSON.stringify(t))", tokens
    )


def load_ha_token(token_arg, token_file_arg):
    """Resolve a Home Assistant token from CLI arg, file, or env var."""
    if token_arg:
        return token_arg.strip()
    if token_file_arg:
        return Path(token_file_arg).expanduser().read_text().strip()
    env = os.environ.get("HA_TOKEN")
    if env:
        return env.strip()
    return None


def screenshot_url(url, page):
    """Take a screenshot of a URL using an existing playwright page."""
    page.goto(url, wait_until="networkidle", timeout=30000)
    png_bytes = page.screenshot(type="png")
    img = Image.open(BytesIO(png_bytes))
    return img


def image_to_jpeg(img, quality=95):
    """Convert a PIL Image to 800x480 JPEG bytes."""
    if img.size != (WIDTH, HEIGHT):
        img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    if img.mode != "RGB":
        img = img.convert("RGB")
    buf = BytesIO()
    img.save(buf, "JPEG", quality=quality, subsampling=0)
    return buf.getvalue()


def build_and_send(jpeg_data, header_template, device):
    """Build container from JPEG and send to screen."""
    header = bytearray(header_template)
    struct.pack_into(">I", header, 0x1000, len(jpeg_data))

    container = bytearray(bytes(header) + jpeg_data)
    total_len = len(container)
    struct.pack_into(">I", container, 0x58, total_len)
    container = bytes(container)

    chunks, end_packet = chunk_container(container, total_len)
    send_upload(device, chunks, end_packet)
    return len(chunks)



def make_browser_context(playwright, auth_state=None, ha_token=None, url=None,
                         session_storage=None):
    """Create a browser context with optional auth and session storage."""
    browser = playwright.chromium.launch()
    ctx_args = {
        "viewport": {"width": WIDTH, "height": HEIGHT},
        "device_scale_factor": 2,
        "color_scheme": "dark",
    }
    if auth_state and Path(auth_state).exists():
        ctx_args["storage_state"] = auth_state
    context = browser.new_context(**ctx_args)
    page = context.new_page()
    if ha_token and url:
        inject_ha_long_lived_token(page, ha_token, url)
    if session_storage and url:
        origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
        if not ha_token:
            page.goto(origin, wait_until="commit", timeout=10000)
        page.evaluate("(items) => { for (let [k,v] of Object.entries(items)) sessionStorage.setItem(k, v); }",
                       session_storage)
    return browser, page


def run_oneshot(source, device, quality, auth_state=None, ha_token=None, session_storage=None):
    """Single push from a URL or image file."""
    header = get_header_template()

    if Path(source).exists():
        img = Image.open(source)
        jpeg_data = image_to_jpeg(img, quality)
    else:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser, page = make_browser_context(p, auth_state, ha_token, source, session_storage)
            img = screenshot_url(source, page)
            jpeg_data = image_to_jpeg(img, quality)
            browser.close()

    print(f"JPEG: {len(jpeg_data)} bytes")
    n = build_and_send(jpeg_data, header, device)
    print(f"Sent {n} chunks")


def run_loop(url, device, interval, quality, auth_state=None, ha_token=None, session_storage=None):
    """Continuous screenshot-and-push loop."""
    header = get_header_template()

    from playwright.sync_api import sync_playwright

    stop = False

    def on_signal(sig, frame):
        nonlocal stop
        print("\nStopping...")
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    with sync_playwright() as p:
        browser, page = make_browser_context(p, auth_state, ha_token, url, session_storage)
        if auth_state:
            print(f"Using auth state from {auth_state}")
        if ha_token:
            print("Using Home Assistant long-lived token")

        print(f"Displaying {url} on {device} every {interval}s")
        print("Press Ctrl+C to stop\n")

        iteration = 0
        while not stop:
            try:
                t0 = time.monotonic()

                img = screenshot_url(url, page)
                jpeg_data = image_to_jpeg(img, quality)
                n = build_and_send(jpeg_data, header, device)

                elapsed = time.monotonic() - t0
                iteration += 1
                print(
                    f"[{iteration}] {n} chunks, {len(jpeg_data)}B JPEG, "
                    f"{elapsed:.1f}s total",
                    flush=True,
                )

                sleep_time = max(0, interval - elapsed)
                if sleep_time > 0 and not stop:
                    time.sleep(sleep_time)

            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"Error: {e}", file=sys.stderr)
                if not stop:
                    time.sleep(interval)

        browser.close()
    print("Stopped.")


def main():
    parser = argparse.ArgumentParser(
        description="Display a web dashboard on the USB smart screen",
    )
    parser.add_argument("url", help="URL to display, or path to an image file")
    parser.add_argument(
        "--device", default="/dev/ttyACM0", help="Serial device (default: /dev/ttyACM0)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Refresh interval in seconds (default: 5, 0 = one-shot)",
    )
    parser.add_argument(
        "--quality",
        type=int,
        default=95,
        help="JPEG quality 1-100 (default: 95)",
    )
    parser.add_argument(
        "--auth-state",
        help="Path to browser state JSON for authenticated sites (saved with capture_auth.py)",
    )
    parser.add_argument(
        "--ha-token",
        help="Home Assistant long-lived access token (also reads $HA_TOKEN)",
    )
    parser.add_argument(
        "--ha-token-file",
        help="Path to a file containing a Home Assistant long-lived access token",
    )
    parser.add_argument(
        "--set-session",
        metavar="KEY=VALUE",
        action="append",
        help="Set sessionStorage key=value before loading (repeatable)",
    )
    args = parser.parse_args()

    session_storage = {}
    if args.set_session:
        for item in args.set_session:
            k, v = item.split("=", 1)
            session_storage[k] = v
    ss = session_storage or None

    ha_token = load_ha_token(args.ha_token, args.ha_token_file)

    if args.interval <= 0 or Path(args.url).exists():
        run_oneshot(args.url, args.device, args.quality, args.auth_state, ha_token, ss)
    else:
        run_loop(args.url, args.device, args.interval, args.quality, args.auth_state, ha_token, ss)


if __name__ == "__main__":
    main()
