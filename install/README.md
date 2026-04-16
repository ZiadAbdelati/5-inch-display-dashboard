# install/

Proxmox LXC installer and supporting files. See the
[main README](../README.md#proxmox-lxc-install) for the full walkthrough.

## Files

| File | Runs on | Purpose |
| --- | --- | --- |
| `proxmox-lxc.sh` | Proxmox host | Creates the LXC, passes through USB, fetches the app, runs bootstrap |
| `container-bootstrap.sh` | Inside LXC | Installs packages, creates service user, builds venv, installs Playwright + Chromium |
| `smart-screen-init` | Inside LXC | Writes `/etc/default/smart-screen` and secrets; use `--interactive` or `--help` |
| `smart-screen-run` | Inside LXC | Service entrypoint (called by systemd, not directly) |
| `smart-screen-update` | Inside LXC | Downloads latest `main` tarball, preserves secrets/venv, restarts service |
| `smart-screen.service` | Inside LXC | Systemd unit — runs as `smartscreen` user with hardening |

## Quick reference

```bash
# Create container (on Proxmox host as root)
bash -c "$(curl -fsSL https://raw.githubusercontent.com/ZiadAbdelati/5-inch-screen/main/install/proxmox-lxc.sh)"

# Initialize (inside container)
smart-screen-init --interactive

# Update (from host)
pct exec <CTID> -- smart-screen-update

# Change settings (inside container)
smart-screen-init --url URL --interval 60
```
