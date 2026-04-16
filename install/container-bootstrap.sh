#!/usr/bin/env bash
# Runs inside the LXC on first install. Sets up the Python app, helper
# commands, and systemd service. Idempotent — safe to re-run for updates
# (it will rebuild the venv and reinstall helpers in place).

set -euo pipefail

APP_DIR="${APP_DIR:-/opt/5-inch-screen}"
SERVICE_USER="${SERVICE_USER:-smartscreen}"

log() { printf '[bootstrap] %s\n' "$*"; }

log "installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl tar \
  python3 python3-pip python3-venv

log "ensuring service user '$SERVICE_USER' exists"
if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# Give the service user access to the serial device. Debian's 'dialout'
# group owns /dev/tty{ACM,USB}*.
usermod -aG dialout "$SERVICE_USER"

log "setting up Python venv at $APP_DIR/.venv"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"
export PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.playwright"
"$APP_DIR/.venv/bin/python" -m playwright install --with-deps chromium

# Secrets dir (owned by service user, 700 perms)
install -d -m 700 -o "$SERVICE_USER" -g "$SERVICE_USER" "$APP_DIR/secrets"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"

log "installing helper commands"
install -m 755 "$APP_DIR/install/smart-screen-run" /usr/local/bin/smart-screen-run
install -m 755 "$APP_DIR/install/smart-screen-init" /usr/local/bin/smart-screen-init
install -m 755 "$APP_DIR/install/smart-screen-update" /usr/local/bin/smart-screen-update

log "installing systemd unit"
install -m 644 "$APP_DIR/install/smart-screen.service" /etc/systemd/system/smart-screen.service
systemctl daemon-reload

log "bootstrap complete"
log "next: run 'smart-screen-init --url <your-dashboard> --prompt-ha-token'"
