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
HOSTNAME=smart-screen \
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
