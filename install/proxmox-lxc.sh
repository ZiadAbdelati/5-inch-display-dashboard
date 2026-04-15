#!/usr/bin/env bash
set -euo pipefail

log() {
  printf '[smart-screen] %s\n' "$*"
}

fail() {
  printf '[smart-screen] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    fail "run this script as root on a Proxmox host"
  fi
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

storage_exists() {
  pvesm status 2>/dev/null | awk 'NR > 1 {print $1}' | grep -Fxq "$1"
}

detect_rootfs_storage() {
  local candidate
  for candidate in local-lvm local-zfs local; do
    if storage_exists "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

detect_template_storage() {
  local candidate
  for candidate in local local-lvm; do
    if storage_exists "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
  return 1
}

detect_usb_device() {
  local by_id_dir="/dev/serial/by-id"
  local candidate

  if [[ -d "$by_id_dir" ]]; then
    while IFS= read -r candidate; do
      [[ -n "$candidate" ]] || continue
      if [[ -e "$candidate" ]]; then
        printf '%s\n' "$candidate"
        return 0
      fi
    done < <(find "$by_id_dir" -maxdepth 1 -type l \( -iname '*1a86*' -o -iname '*qinheng*' -o -iname '*cdc-serial*' \) | sort)
  fi

  for candidate in /dev/ttyACM0 /dev/ttyUSB0; do
    if [[ -e "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

find_template_name() {
  local pattern="${1:-debian-12-standard}"
  local template

  pveam update >/dev/null
  template="$(
    pveam available --section system 2>/dev/null \
      | awk -v pat="$pattern" '$0 ~ pat && $0 ~ /amd64.tar.zst/ {print $2}' \
      | sort -V \
      | tail -n 1
  )"
  [[ -n "$template" ]] || fail "could not find an LXC template matching $pattern"
  printf '%s\n' "$template"
}

ensure_template_downloaded() {
  local storage="$1"
  local template="$2"

  if pveam list "$storage" 2>/dev/null | awk '{print $2}' | grep -Fxq "$template"; then
    return 0
  fi

  log "downloading template $template to storage $storage"
  pveam download "$storage" "$template"
}

append_lxc_line() {
  local config_file="$1"
  local line="$2"

  if ! grep -Fqx "$line" "$config_file"; then
    printf '%s\n' "$line" >>"$config_file"
  fi
}

create_bootstrap_script() {
  local bootstrap_file="$1"
  local repo_url="$2"
  local repo_branch="$3"
  local app_dir="$4"

  cat >"$bootstrap_file" <<EOF
#!/usr/bin/env bash
set -euo pipefail

APP_DIR=$(printf '%q' "$app_dir")
REPO_URL=$(printf '%q' "$repo_url")
REPO_BRANCH=$(printf '%q' "$repo_branch")

export DEBIAN_FRONTEND=noninteractive

apt-get update
apt-get install -y --no-install-recommends \
  bash \
  ca-certificates \
  curl \
  git \
  python3 \
  python3-pip \
  python3-venv

rm -rf "\$APP_DIR"
git clone --depth 1 --branch "\$REPO_BRANCH" "\$REPO_URL" "\$APP_DIR"

python3 -m venv "\$APP_DIR/.venv"
"\$APP_DIR/.venv/bin/pip" install --upgrade pip
"\$APP_DIR/.venv/bin/pip" install -r "\$APP_DIR/requirements.txt"
"\$APP_DIR/.venv/bin/python" -m playwright install --with-deps chromium

install -d -m 700 "\$APP_DIR/secrets"

cat >/usr/local/bin/smart-screen-run <<'RUNEOF'
#!/usr/bin/env bash
set -euo pipefail

CONFIG_FILE=/etc/default/smart-screen
[[ -r "\$CONFIG_FILE" ]] || {
  echo "missing config: \$CONFIG_FILE" >&2
  exit 1
}

source "\$CONFIG_FILE"

: "\${SMART_SCREEN_URL:?SMART_SCREEN_URL is required}"

APP_DIR="\${SMART_SCREEN_APP_DIR:-/opt/5-inch-screen}"
PYTHON_BIN="\$APP_DIR/.venv/bin/python"
SCRIPT_PATH="\$APP_DIR/src/screen_daemon.py"
DEVICE="\${SMART_SCREEN_DEVICE:-/dev/smart-screen}"
INTERVAL="\${SMART_SCREEN_INTERVAL:-30}"
QUALITY="\${SMART_SCREEN_QUALITY:-85}"

args=(
  "\$SCRIPT_PATH"
  "\$SMART_SCREEN_URL"
  --device "\$DEVICE"
  --interval "\$INTERVAL"
  --quality "\$QUALITY"
)

if [[ -n "\${SMART_SCREEN_HA_TOKEN_FILE:-}" ]]; then
  args+=(--ha-token-file "\$SMART_SCREEN_HA_TOKEN_FILE")
fi

if [[ -n "\${SMART_SCREEN_AUTH_STATE:-}" ]]; then
  args+=(--auth-state "\$SMART_SCREEN_AUTH_STATE")
fi

if declare -p SMART_SCREEN_EXTRA_ARGS >/dev/null 2>&1; then
  args+=("\${SMART_SCREEN_EXTRA_ARGS[@]}")
fi

exec "\$PYTHON_BIN" "\${args[@]}"
RUNEOF
chmod 755 /usr/local/bin/smart-screen-run

cat >/usr/local/bin/smart-screen-init <<'INITEOF'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR=$(printf '%q' "$app_dir")
CONFIG_FILE=/etc/default/smart-screen
SECRETS_DIR=\$APP_DIR/secrets
TOKEN_DEST=\$SECRETS_DIR/ha_token
AUTH_DEST=\$SECRETS_DIR/auth_state.json

usage() {
  cat <<'USAGE'
Usage:
  smart-screen-init --url URL [options]

Options:
  --url URL                 Dashboard URL to display
  --device PATH             Serial device inside container (default: /dev/smart-screen)
  --interval SECONDS        Refresh interval (default: 30)
  --quality N               JPEG quality 1..100 (default: 85)
  --ha-token TOKEN          Save this Home Assistant long-lived token
  --ha-token-file PATH      Copy token from a file into the standard secrets path
  --prompt-ha-token         Prompt securely for the Home Assistant token
  --auth-state PATH         Copy a Playwright auth-state JSON file for cookie auth
  --set-session KEY=VALUE   Add a sessionStorage item; may be repeated
  --start-now               Restart and enable the service after writing config (default)
  --no-start                Only write config and secrets
  --help                    Show this message
USAGE
}

require_value() {
  local flag="\$1"
  local value="\${2:-}"
  [[ -n "\$value" ]] || {
    echo "missing value for \$flag" >&2
    exit 1
  }
}

URL=
DEVICE=/dev/smart-screen
INTERVAL=30
QUALITY=85
HA_TOKEN=
HA_TOKEN_FILE=
PROMPT_TOKEN=0
AUTH_STATE=
START_NOW=1
EXTRA_ARGS=()

while (($#)); do
  case "\$1" in
    --url)
      require_value "\$1" "\${2:-}"
      URL="\$2"
      shift 2
      ;;
    --device)
      require_value "\$1" "\${2:-}"
      DEVICE="\$2"
      shift 2
      ;;
    --interval)
      require_value "\$1" "\${2:-}"
      INTERVAL="\$2"
      shift 2
      ;;
    --quality)
      require_value "\$1" "\${2:-}"
      QUALITY="\$2"
      shift 2
      ;;
    --ha-token)
      require_value "\$1" "\${2:-}"
      HA_TOKEN="\$2"
      shift 2
      ;;
    --ha-token-file)
      require_value "\$1" "\${2:-}"
      HA_TOKEN_FILE="\$2"
      shift 2
      ;;
    --prompt-ha-token)
      PROMPT_TOKEN=1
      shift
      ;;
    --auth-state)
      require_value "\$1" "\${2:-}"
      AUTH_STATE="\$2"
      shift 2
      ;;
    --set-session)
      require_value "\$1" "\${2:-}"
      EXTRA_ARGS+=(--set-session "\$2")
      shift 2
      ;;
    --start-now)
      START_NOW=1
      shift
      ;;
    --no-start)
      START_NOW=0
      shift
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "unknown option: \$1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

[[ -n "\$URL" ]] || {
  echo "--url is required" >&2
  usage >&2
  exit 1
}

install -d -m 700 "\$SECRETS_DIR"

if [[ \$PROMPT_TOKEN -eq 1 ]]; then
  read -r -s -p "Home Assistant long-lived token: " HA_TOKEN
  echo
fi

if [[ -n "\$HA_TOKEN" ]]; then
  printf '%s\n' "\$HA_TOKEN" >"\$TOKEN_DEST"
  chmod 600 "\$TOKEN_DEST"
elif [[ -n "\$HA_TOKEN_FILE" ]]; then
  install -m 600 "\$HA_TOKEN_FILE" "\$TOKEN_DEST"
fi

if [[ -n "\$AUTH_STATE" ]]; then
  install -m 600 "\$AUTH_STATE" "\$AUTH_DEST"
fi

{
  echo "# Generated by smart-screen-init"
  printf 'SMART_SCREEN_APP_DIR=%q\n' "\$APP_DIR"
  printf 'SMART_SCREEN_URL=%q\n' "\$URL"
  printf 'SMART_SCREEN_DEVICE=%q\n' "\$DEVICE"
  printf 'SMART_SCREEN_INTERVAL=%q\n' "\$INTERVAL"
  printf 'SMART_SCREEN_QUALITY=%q\n' "\$QUALITY"

  if [[ -f "\$TOKEN_DEST" ]]; then
    printf 'SMART_SCREEN_HA_TOKEN_FILE=%q\n' "\$TOKEN_DEST"
  else
    echo 'SMART_SCREEN_HA_TOKEN_FILE='
  fi

  if [[ -f "\$AUTH_DEST" ]]; then
    printf 'SMART_SCREEN_AUTH_STATE=%q\n' "\$AUTH_DEST"
  else
    echo 'SMART_SCREEN_AUTH_STATE='
  fi

  echo 'SMART_SCREEN_EXTRA_ARGS=('
  for arg in "\${EXTRA_ARGS[@]}"; do
    printf '  %q\n' "\$arg"
  done
  echo ')'
} >"\$CONFIG_FILE"

chmod 600 "\$CONFIG_FILE"
systemctl daemon-reload
systemctl enable smart-screen.service >/dev/null

if [[ \$START_NOW -eq 1 ]]; then
  systemctl restart smart-screen.service
  systemctl --no-pager --full status smart-screen.service || true
else
  echo "Config written to \$CONFIG_FILE"
  echo "Run: systemctl restart smart-screen.service"
fi
INITEOF
chmod 755 /usr/local/bin/smart-screen-init

cat >/etc/systemd/system/smart-screen.service <<'SERVICEEOF'
[Unit]
Description=5-inch smart screen dashboard daemon
Wants=network-online.target
After=network-online.target
ConditionPathExists=/etc/default/smart-screen

[Service]
Type=simple
User=root
WorkingDirectory=/opt/5-inch-screen/src
ExecStart=/usr/local/bin/smart-screen-run
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SERVICEEOF

systemctl daemon-reload
EOF

  chmod 755 "$bootstrap_file"
}

main() {
  require_root
  require_cmd pct
  require_cmd pveam
  require_cmd pvesh
  require_cmd pvesm

  local repo_url="${REPO_URL:-https://github.com/ZiadAbdelati/5-inch-screen.git}"
  local repo_branch="${REPO_BRANCH:-main}"
  local app_dir="${APP_DIR:-/opt/5-inch-screen}"
  local ctid="${CTID:-$(pvesh get /cluster/nextid)}"
  local hostname="${HOSTNAME:-smart-screen}"
  local storage="${STORAGE:-$(detect_rootfs_storage || true)}"
  local template_storage="${TEMPLATE_STORAGE:-$(detect_template_storage || true)}"
  local bridge="${BRIDGE:-vmbr0}"
  local disk_gb="${DISK_GB:-8}"
  local cores="${CORES:-2}"
  local memory_mb="${MEMORY_MB:-2048}"
  local swap_mb="${SWAP_MB:-512}"
  local usb_device="${USB_DEVICE:-$(detect_usb_device || true)}"
  local inside_device="${INSIDE_DEVICE:-/dev/smart-screen}"
  local template_pattern="${TEMPLATE_PATTERN:-debian-12-standard}"
  local template_name
  local config_file="/etc/pve/lxc/${ctid}.conf"
  local temp_dir
  local bootstrap_file

  [[ -n "$storage" ]] || fail "could not detect a rootfs storage; set STORAGE explicitly"
  [[ -n "$template_storage" ]] || fail "could not detect a template storage; set TEMPLATE_STORAGE explicitly"
  [[ -n "$usb_device" ]] || fail "could not detect the smart screen USB device; set USB_DEVICE explicitly"

  if pct status "$ctid" >/dev/null 2>&1; then
    fail "container ID $ctid already exists"
  fi

  template_name="$(find_template_name "$template_pattern")"
  ensure_template_downloaded "$template_storage" "$template_name"

  log "creating LXC $ctid ($hostname)"
  pct create "$ctid" "${template_storage}:vztmpl/${template_name}" \
    --arch amd64 \
    --cores "$cores" \
    --hostname "$hostname" \
    --memory "$memory_mb" \
    --net0 "name=eth0,bridge=${bridge},ip=dhcp" \
    --onboot 1 \
    --ostype debian \
    --rootfs "${storage}:${disk_gb}" \
    --swap "$swap_mb" \
    --unprivileged 0

  append_lxc_line "$config_file" "lxc.cgroup2.devices.allow: c 166:* rwm"
  append_lxc_line "$config_file" "lxc.mount.entry: ${usb_device} dev/$(basename "$inside_device") none bind,optional,create=file"

  log "starting LXC $ctid"
  pct start "$ctid"

  temp_dir="$(mktemp -d)"
  bootstrap_file="$temp_dir/bootstrap-smart-screen.sh"
  create_bootstrap_script "$bootstrap_file" "$repo_url" "$repo_branch" "$app_dir"

  pct push "$ctid" "$bootstrap_file" /root/bootstrap-smart-screen.sh >/dev/null
  log "bootstrapping application inside the container"
  pct exec "$ctid" -- bash /root/bootstrap-smart-screen.sh
  pct exec "$ctid" -- rm -f /root/bootstrap-smart-screen.sh

  rm -rf "$temp_dir"

  log "installation complete"
  printf '\n'
  printf 'Container ID: %s\n' "$ctid"
  printf 'Container name: %s\n' "$hostname"
  printf 'Inside-device path: %s\n' "$inside_device"
  printf '\n'
  printf 'Next step:\n'
  printf '  pct enter %s\n' "$ctid"
  printf '  smart-screen-init --url https://ha.example.com/dashboard --prompt-ha-token\n'
  printf '\n'
  printf 'If your screen does not stay at the same host path, set USB_DEVICE to a stable host path before install,\n'
  printf 'for example a /dev/serial/by-id/... symlink created by udev.\n'
}

main "$@"
