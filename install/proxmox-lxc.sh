#!/usr/bin/env bash
# Host-side Proxmox installer for the 5-inch smart screen dashboard daemon.
# Creates a Debian 12 LXC, passes through the USB serial device, downloads
# the app, and runs the container-side bootstrap.
#
# Run as root on a Proxmox host. Env-var overrides: see --help.

set -euo pipefail

# --- defaults ---
REPO_URL_DEFAULT="https://github.com/ZiadAbdelati/5-inch-screen"
REPO_BRANCH_DEFAULT="main"
APP_DIR_DEFAULT="/opt/5-inch-screen"
CT_HOSTNAME_DEFAULT="smart-screen"
BRIDGE_DEFAULT="vmbr0"
DISK_GB_DEFAULT="8"
CORES_DEFAULT="2"
MEMORY_MB_DEFAULT="2048"
SWAP_MB_DEFAULT="512"
INSIDE_DEVICE_DEFAULT="/dev/smart-screen"
TEMPLATE_PATTERN_DEFAULT="debian-12-standard"

log()  { printf '[smart-screen] %s\n' "$*"; }
warn() { printf '[smart-screen] WARN: %s\n' "$*" >&2; }
fail() { printf '[smart-screen] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Usage:
  bash proxmox-lxc.sh [--help]

Host-side installer. Creates a Debian LXC, passes through the smart screen
USB serial device, fetches the app, and bootstraps it inside the container.

Env-var overrides (all optional):
  CTID                Container ID (default: pvesh get /cluster/nextid)
  CT_HOSTNAME         Container hostname (default: smart-screen)
  STORAGE             Rootfs storage pool (default: auto-detect)
  TEMPLATE_STORAGE    Template storage pool (default: auto-detect)
  TEMPLATE_PATTERN    Template name pattern (default: debian-12-standard)
  BRIDGE              Network bridge (default: vmbr0)
  DISK_GB             Rootfs disk size in GB (default: 8)
  CORES               CPU cores (default: 2)
  MEMORY_MB           RAM in MB (default: 2048)
  SWAP_MB             Swap in MB (default: 512)
  USB_DEVICE          Host path to the screen (default: auto-detect via
                      /dev/serial/by-id/* then /dev/ttyACM0)
  INSIDE_DEVICE       Device path inside the LXC (default: /dev/smart-screen)
  REPO_URL            App repository URL (default: ZiadAbdelati/5-inch-screen)
  REPO_BRANCH         Repo branch (default: main)
  APP_DIR             Install directory inside the container (default:
                      /opt/5-inch-screen)

The resulting container is privileged (--unprivileged 0) so USB serial
passthrough works without uid/gid remapping. If that is unacceptable for
your environment, edit the 'pct create' call below before running.

After install:
  pct enter <CTID>
  smart-screen-init --url https://ha.example.com/dashboard --prompt-ha-token
USAGE
}

# --- early arg handling (only --help) ---
for arg in "$@"; do
  case "$arg" in
    -h|--help) usage; exit 0 ;;
    *) fail "unknown argument: $arg (try --help)" ;;
  esac
done

# --- prerequisite checks ---
require_root() {
  [[ ${EUID:-$(id -u)} -eq 0 ]] || fail "run this script as root on a Proxmox host"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "missing required command: $1"
}

# --- helpers ---
storage_exists() {
  pvesm status 2>/dev/null | awk 'NR > 1 {print $1}' | grep -Fxq "$1"
}

detect_rootfs_storage() {
  local candidate
  for candidate in local-lvm local-zfs local; do
    storage_exists "$candidate" && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

detect_template_storage() {
  local candidate
  for candidate in local local-lvm; do
    storage_exists "$candidate" && { printf '%s\n' "$candidate"; return 0; }
  done
  return 1
}

# Look up a stable USB-by-id path first, then fall back to /dev/ttyACM0.
# Returns the path on stdout; returns 1 if nothing plausible was found.
detect_usb_device() {
  local by_id_dir="/dev/serial/by-id"
  if [[ -d "$by_id_dir" ]]; then
    # Any CDC-ACM / USB-serial-ish device is a candidate. We don't filter
    # on vendor strings — your adapter may not match the common patterns.
    local first
    first="$(find "$by_id_dir" -maxdepth 1 -type l | sort | head -n 1 || true)"
    if [[ -n "$first" ]]; then
      printf '%s\n' "$first"
      return 0
    fi
  fi
  if [[ -e /dev/ttyACM0 ]]; then
    printf '/dev/ttyACM0\n'
    return 0
  fi
  return 1
}

find_template_name() {
  local pattern="$1" template
  pveam update >/dev/null
  template="$(pveam available --section system 2>/dev/null \
    | awk -v pat="$pattern" '$0 ~ pat && $0 ~ /amd64.tar.zst/ {print $2}' \
    | sort -V | tail -n 1)"
  [[ -n "$template" ]] || fail "could not find an LXC template matching $pattern"
  printf '%s\n' "$template"
}

ensure_template_downloaded() {
  local storage="$1" template="$2"
  if pveam list "$storage" 2>/dev/null | awk '{print $2}' | grep -Fq "$template"; then
    return 0
  fi
  log "downloading template $template to storage $storage"
  pveam download "$storage" "$template"
}

append_lxc_line() {
  local config_file="$1" line="$2"
  grep -Fqx "$line" "$config_file" || printf '%s\n' "$line" >> "$config_file"
}

# --- main ---
main() {
  require_root
  require_cmd pct
  require_cmd pveam
  require_cmd pvesh
  require_cmd pvesm
  require_cmd curl

  local repo_url="${REPO_URL:-$REPO_URL_DEFAULT}"
  local repo_branch="${REPO_BRANCH:-$REPO_BRANCH_DEFAULT}"
  local app_dir="${APP_DIR:-$APP_DIR_DEFAULT}"
  local ctid="${CTID:-$(pvesh get /cluster/nextid)}"
  local ct_hostname="${CT_HOSTNAME:-$CT_HOSTNAME_DEFAULT}"
  local storage="${STORAGE:-$(detect_rootfs_storage || true)}"
  local template_storage="${TEMPLATE_STORAGE:-$(detect_template_storage || true)}"
  local bridge="${BRIDGE:-$BRIDGE_DEFAULT}"
  local disk_gb="${DISK_GB:-$DISK_GB_DEFAULT}"
  local cores="${CORES:-$CORES_DEFAULT}"
  local memory_mb="${MEMORY_MB:-$MEMORY_MB_DEFAULT}"
  local swap_mb="${SWAP_MB:-$SWAP_MB_DEFAULT}"
  local usb_device="${USB_DEVICE:-$(detect_usb_device || true)}"
  local inside_device="${INSIDE_DEVICE:-$INSIDE_DEVICE_DEFAULT}"
  local template_pattern="${TEMPLATE_PATTERN:-$TEMPLATE_PATTERN_DEFAULT}"

  [[ -n "$storage"          ]] || fail "could not detect a rootfs storage; set STORAGE"
  [[ -n "$template_storage" ]] || fail "could not detect a template storage; set TEMPLATE_STORAGE"
  [[ -n "$usb_device"       ]] || fail "could not detect a USB serial device; set USB_DEVICE (try ls /dev/serial/by-id)"

  if [[ "$usb_device" != /dev/serial/by-id/* ]]; then
    warn "using $usb_device — this path can change across reboots or device replugs"
    warn "prefer a /dev/serial/by-id/... symlink; see 'ls /dev/serial/by-id/' on the host"
  fi

  if pct status "$ctid" >/dev/null 2>&1; then
    fail "container ID $ctid already exists; set CTID to a free ID or destroy it first"
  fi

  local archive_url="${repo_url%/}/archive/refs/heads/${repo_branch}.tar.gz"

  local template_name
  template_name="$(find_template_name "$template_pattern")"
  ensure_template_downloaded "$template_storage" "$template_name"

  log "creating LXC $ctid ($ct_hostname) on $storage, ${cores}c/${memory_mb}MB/${disk_gb}GB"

  # Cleanup-on-failure trap: if anything after pct create fails, destroy the
  # container so the user is not left with a half-configured CT to delete
  # manually.
  local created=0
  cleanup_on_fail() {
    local rc=$?
    if [[ $rc -ne 0 && $created -eq 1 ]]; then
      warn "installation failed (exit $rc); destroying container $ctid"
      pct stop "$ctid" --skiplock 1 >/dev/null 2>&1 || true
      pct destroy "$ctid" --purge 1 >/dev/null 2>&1 || true
    fi
    exit $rc
  }
  trap cleanup_on_fail EXIT

  pct create "$ctid" "${template_storage}:vztmpl/${template_name}" \
    --arch amd64 \
    --cores "$cores" \
    --features nesting=1,keyctl=1 \
    --hostname "$ct_hostname" \
    --memory "$memory_mb" \
    --net0 "name=eth0,bridge=${bridge},ip=dhcp" \
    --onboot 1 \
    --ostype debian \
    --rootfs "${storage}:${disk_gb}" \
    --swap "$swap_mb" \
    --unprivileged 0
  created=1

  local config_file="/etc/pve/lxc/${ctid}.conf"
  # 166 = CDC-ACM char major; covers /dev/ttyACM*. If you pass through a
  # /dev/ttyUSB* instead, you also need 'c 188:* rwm'.
  append_lxc_line "$config_file" "lxc.cgroup2.devices.allow: c 166:* rwm"
  append_lxc_line "$config_file" "lxc.cgroup2.devices.allow: c 188:* rwm"
  append_lxc_line "$config_file" \
    "lxc.mount.entry: ${usb_device} dev/$(basename "$inside_device") none bind,optional,create=file"

  log "starting LXC $ctid"
  pct start "$ctid"

  # Wait a few seconds for network to come up inside the container.
  local tries=0
  until pct exec "$ctid" -- getent hosts deb.debian.org >/dev/null 2>&1; do
    tries=$((tries+1))
    (( tries > 15 )) && fail "container has no network after 15s"
    sleep 1
  done

  log "fetching $archive_url inside the container"
  # Install curl if the template doesn't have it, then download + extract.
  pct exec "$ctid" -- bash -c "
    set -euo pipefail
    export DEBIAN_FRONTEND=noninteractive
    command -v curl >/dev/null 2>&1 || {
      apt-get update
      apt-get install -y --no-install-recommends curl ca-certificates
    }
    rm -rf '$app_dir'
    mkdir -p '$app_dir'
    curl -fsSL '$archive_url' | tar -xz --strip-components=1 -C '$app_dir'
  "

  log "running container bootstrap"
  pct exec "$ctid" -- bash "$app_dir/install/container-bootstrap.sh"

  trap - EXIT

  log "installation complete"
  cat <<EOF

  Container ID: $ctid
  Hostname:     $ct_hostname
  USB device:   $usb_device -> $inside_device (inside)
  App dir:      $app_dir (inside)

  Next:
    pct enter $ctid
    smart-screen-init --url https://ha.example.com/dashboard --prompt-ha-token

  To update later:
    pct exec $ctid -- smart-screen-update

EOF
}

main "$@"
