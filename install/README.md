# Proxmox LXC installer

The main entrypoint is `install/proxmox-lxc.sh`. Run it on a Proxmox host as
root.

Example:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ZiadAbdelati/5-inch-screen/main/install/proxmox-lxc.sh)"
```

Useful overrides:

```bash
CTID=120 \
CT_HOSTNAME=smart-screen \
STORAGE=local-lvm \
TEMPLATE_STORAGE=local \
USB_DEVICE=/dev/serial/by-id/usb-1a86_USB_CDC-Serial_20191234-if00 \
bash install/proxmox-lxc.sh
```

If `USB_DEVICE` is omitted, the installer tries to auto-detect the device and
prefers a stable `/dev/serial/by-id/...` path over `/dev/ttyACM0`.

After install, enter the container and initialize the daemon:

```bash
pct enter 120
smart-screen-init --url https://ha.example.com/dashboard --prompt-ha-token
```

The initializer writes the token to `/opt/5-inch-screen/secrets/ha_token`,
builds `/etc/default/smart-screen`, enables `smart-screen.service`, and
restarts it.

The installer enables `nesting=1,keyctl=1` on the LXC to avoid the common
systemd 252 warning on Proxmox and to give Chromium a less constrained
container environment.

## Updating later

```bash
pct exec <CTID> -- smart-screen-update
```

Downloads the latest `main` tarball, preserves `secrets/` and `.venv/`,
reinstalls Python deps, refreshes the helper scripts and systemd unit, and
restarts the service. The previous install is swapped to `/opt/5-inch-screen.old`
briefly before being removed so a failed update leaves the old tree intact.

## Helper scripts (installed inside the container)

| Command | Purpose |
| --- | --- |
| `smart-screen-init` | Writes `/etc/default/smart-screen` and optional secrets; `--help` for flags |
| `smart-screen-run` | Service entrypoint; sourced by the systemd unit, not called directly |
| `smart-screen-update` | Pulls the latest `main` tarball and restarts the service |

## Container bootstrap

`proxmox-lxc.sh` runs `install/container-bootstrap.sh` inside the new LXC.
That script installs system packages, creates the `smartscreen` service user
(added to `dialout`), builds the Python virtualenv, installs Playwright with
Chromium plus its system deps, and drops the helper scripts and systemd unit
into place.
