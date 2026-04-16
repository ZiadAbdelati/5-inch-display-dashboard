# 5-inch smart screen dashboard daemon

Displays any web dashboard on a 5" 800x480 USB "smart screen" (the type sold as
a mini PC hardware monitor). The daemon screenshots a URL at 800x480 and pushes
it to the screen over USB serial on a configurable interval — no vendor software,
no Windows required.

Designed for headless use on Linux (bare metal, LXC, Docker, or a Proxmox node).

## Features

- Display any URL or local image
- Home Assistant support via long-lived access tokens (headless-friendly)
- Cookie-based auth for other dashboards (Grafana, Pulse, etc.) via
  `capture_auth.py`
- Dark-mode viewport by default
- No black-flash on live values (sensor packets are flicker-free); background
  updates incur one flash per refresh

## Hardware

Tested with a 5" 800x480 USB smart screen that enumerates as a CDC-ACM serial
device (`/dev/ttyACM0`). The screen is recognized by the vendor software
"5 inch SmartMonitor V4" on Windows. The protocol it speaks was reverse-
engineered by capturing serial traffic; see `src/parser.py` and
`src/substitute_jpeg.py`.

## Quick start (bare metal)

Requires Python 3.10+.

```bash
git clone https://github.com/ZiadAbdelati/5-inch-screen.git
cd 5-inch-screen
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

On Linux, add your user to the `uucp` (or `dialout`) group so you can access
the serial device without sudo:

```bash
sudo usermod -aG uucp $USER   # Arch; use 'dialout' on Debian/Ubuntu
# log out and back in
```

## Proxmox LXC install

A one-line installer creates a privileged Debian 12 LXC, passes through the
USB serial device, installs the app, and sets up a systemd service.

### 1. Create the container

Run on the Proxmox host as root:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ZiadAbdelati/5-inch-screen/main/install/proxmox-lxc.sh)"
```

The installer auto-detects the USB device (preferring a stable
`/dev/serial/by-id/...` symlink) and the next free container ID. Override
anything with environment variables:

```bash
CTID=120 \
CT_HOSTNAME=smart-screen \
USB_DEVICE=/dev/serial/by-id/usb-1a86_USB_CDC-Serial_20191234-if00 \
STORAGE=local-lvm \
BRIDGE=vmbr0 \
bash install/proxmox-lxc.sh
```

Run `bash install/proxmox-lxc.sh --help` for the full list of overrides.

If anything fails after the container is created, the installer automatically
destroys it so you don't have to clean up a half-configured CT.

### 2. Initialize the daemon

Enter the container and run the interactive setup:

```bash
pct enter <CTID>
smart-screen-init --interactive
```

This prompts for everything with defaults in brackets:

```
Dashboard URL: https://ha.example.com/dashboard
Refresh interval in seconds [30]: 60
JPEG quality 1-100 [85]:
Serial device path [/dev/smart-screen]:
Home Assistant token (blank to skip): <paste token>
```

Or pass everything as flags (useful for scripting):

```bash
smart-screen-init \
  --url https://ha.example.com/dashboard \
  --interval 60 \
  --quality 85 \
  --prompt-ha-token
```

The initializer writes `/etc/default/smart-screen`, saves the HA token to
`/opt/5-inch-screen/secrets/ha_token` (mode 600), enables the systemd service,
and starts it.

#### Other token options

```bash
# Pass a token directly
smart-screen-init --url URL --ha-token 'eyJ...'

# Read token from a file
smart-screen-init --url URL --ha-token-file /root/ha_token

# Secure prompt at stdin
smart-screen-init --url URL --prompt-ha-token
```

### 3. Verify it's running

```bash
systemctl status smart-screen.service
journalctl -u smart-screen.service -f
```

### Updating

From the Proxmox host:

```bash
pct exec <CTID> -- smart-screen-update
```

Downloads the latest `main` tarball, preserves `secrets/` and `.venv/`,
reinstalls Python deps, refreshes the helper scripts and systemd unit, and
restarts the service.

### Changing settings

Re-run `smart-screen-init` with the new values. It overwrites
`/etc/default/smart-screen` and restarts the service:

```bash
smart-screen-init --url https://ha.example.com/dashboard --interval 60
```

Or use `--interactive` to be prompted for each setting again.

### Helper commands (inside the container)

| Command | Purpose |
| --- | --- |
| `smart-screen-init` | Writes runtime config and secrets; `--help` for all flags |
| `smart-screen-run` | Service entrypoint (called by systemd, not directly) |
| `smart-screen-update` | Pulls latest code from GitHub and restarts the service |

### Container details

- **Privileged** (`--unprivileged 0`) for USB serial passthrough without
  uid/gid remapping
- **nesting=1, keyctl=1** to avoid systemd 252 warnings and give Chromium a
  less constrained environment
- **cgroup allows** for char majors 166 (ttyACM) and 188 (ttyUSB)
- Service runs as a dedicated `smartscreen` user in the `dialout` group
- Systemd hardening: `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=strict`,
  `ProtectHome`, `ReadWritePaths=/opt/5-inch-screen`

## Usage (manual / bare metal)

### Home Assistant

1. In HA: **Profile → Security → Create Long-Lived Access Token**.
2. Save the token somewhere the daemon can read it:

   ```bash
   echo 'eyJ...' > ~/.ha_token
   chmod 600 ~/.ha_token
   ```

3. Run the daemon:

   ```bash
   cd src
   python screen_daemon.py https://ha.example.com/dashboard-of-your-choice \
       --ha-token-file ~/.ha_token --interval 30
   ```

   Or with an env var:

   ```bash
   HA_TOKEN='eyJ...' python screen_daemon.py https://ha.example.com/dashboard --interval 30
   ```

### Other dashboards (cookie auth)

```bash
python capture_auth.py https://dashboard.example.com auth_state.json
# log in / set up the page in the visible browser, then press Enter
python screen_daemon.py https://dashboard.example.com --auth-state auth_state.json
```

### Unauthenticated URLs and local images

```bash
python screen_daemon.py http://homeassistant.local:8123/lovelace/0
python screen_daemon.py ./my-background.png --interval 0   # one-shot
```

### Full CLI options

```
screen_daemon.py URL [options]

  --device PATH         Serial device (default /dev/ttyACM0)
  --interval SECONDS    Refresh interval; 0 = one-shot (default 5)
  --quality 1..100      JPEG quality (default 85)
  --ha-token TOKEN      Home Assistant long-lived token (or $HA_TOKEN)
  --ha-token-file PATH  Read token from file
  --auth-state PATH     Browser state JSON (for cookie auth)
  --set-session K=V     Set sessionStorage before loading (repeatable)
```

## Project layout

```
src/
  screen_daemon.py       Main daemon (CLI entry point)
  substitute_jpeg.py     USB protocol: CRC, chunking, upload
  parser.py              DMS capture parser + container reconstructor
  capture_auth.py        Browser state capture helper
  header_template.bin    Cached theme container header (auto-generated)
  synth_theme.py         Experimental: synthesize minimal themes
  test_*.py              Experiments from protocol reverse-engineering

install/
  proxmox-lxc.sh         Host-side Proxmox installer
  container-bootstrap.sh Runs inside the LXC on first install
  smart-screen-init      Config/secrets helper (--help for flags)
  smart-screen-run       Service entrypoint (sourced by systemd unit)
  smart-screen-update    Tarball-based updater
  smart-screen.service   Systemd unit with hardening
```

The `test_*.py` scripts require a capture file under `captures/` that is not
part of this repo; they are preserved for reference only.

## Status

Working: screenshot-based dashboard display (the primary use case).

Experimental / WIP: native widget themes with live sensor data. Background
swaps cause a ~1s black flash; native widget updates via 0x66 sensor packets
are flicker-free but require a valid vendor-generated theme container (not
yet feasible to generate ourselves). See issues for progress.

## License

MIT
