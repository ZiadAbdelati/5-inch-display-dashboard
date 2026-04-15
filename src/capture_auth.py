#!/usr/bin/env python3
"""Capture a browser storage state for dashboards that need cookie/session auth.

Opens a visible browser so you can log in, enable kiosk mode, or otherwise
set the page up. Then saves cookies + localStorage for headless replay by
screen_daemon.py via --auth-state.

For Home Assistant, do NOT use this script; generate a long-lived access
token in HA (Profile -> Security -> Create Long-Lived Access Token) and
pass it to screen_daemon.py via --ha-token, --ha-token-file, or $HA_TOKEN.

Usage:
    capture_auth.py <url> [output_file]
    capture_auth.py https://dashboard.example.com auth_state.json
"""
import sys
from playwright.sync_api import sync_playwright

url = sys.argv[1] if len(sys.argv) > 1 else None
if not url:
    print(__doc__)
    sys.exit(1)
out = sys.argv[2] if len(sys.argv) > 2 else "auth_state.json"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(viewport={"width": 800, "height": 480}, color_scheme="dark")
    page = context.new_page()
    page.goto(url, wait_until="networkidle", timeout=60000)

    input("Set up the page (log in, enable kiosk mode, etc.), then press Enter... ")

    context.storage_state(path=out)
    print(f"Browser state saved to {out}")
    print(f"  Use with: screen_daemon.py <url> --auth-state {out}")

    browser.close()
