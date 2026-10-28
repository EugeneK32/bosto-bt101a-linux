#!/usr/bin/env bash
# One-time installer for Arch Linux + GNOME Wayland.
set -Eeuo pipefail

HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/.local/share/bosto-hermes"
UNIT_DIR="$HOME/.config/systemd/user"
RULE=/etc/udev/rules.d/70-bosto-hermes.rules
HERMES_REPO_URL=https://github.com/MrOz59/Hermes-KMS.git
HERMES_LOCAL="$HOME/proj/bosto-monitor/Hermes-KMS"
HERMES_CACHE="$HOME/.cache/bosto-hermes/Hermes-KMS"
LOCK="$HERE/hermes.lock"

info() { printf '\n[bosto] %s\n' "$*"; }
fail() { printf '\n[bosto] ERROR: %s\n' "$*" >&2; exit 1; }

[[ "$(id -u)" != 0 ]] || fail 'Run as your normal user, WITHOUT sudo: ./install.sh'
command -v pacman >/dev/null || fail 'This installer is for Arch Linux / pacman.'
[[ -f "$HERE/bosto_hermes.py" && -f "$HERE/hermes_capture.c" ]] || fail 'Incomplete archive.'

info 'Installing dependencies'
sudo pacman -S --needed base-devel git dkms acl libdrm libusb python python-pillow python-pyusb python-cryptography

# DKMS needs headers for the kernel currently running, not just any kernel.
if [[ ! -e "/usr/lib/modules/$(uname -r)/build/Makefile" ]]; then
  kernel_pkg="$(cat "/usr/lib/modules/$(uname -r)/pkgbase" 2>/dev/null || true)"
  [[ -n "$kernel_pkg" && "$kernel_pkg" =~ ^[a-zA-Z0-9_+.-]+$ ]] || fail "Kernel headers missing for $(uname -r). Install matching headers manually, then rerun."
  info "Installing kernel headers: ${kernel_pkg}-headers"
  sudo pacman -S --needed "${kernel_pkg}-headers"
fi
[[ -e "/usr/lib/modules/$(uname -r)/build/Makefile" ]] || fail 'Kernel headers are still missing. If kernel was updated, reboot into the installed kernel.'

# When the user's existing Hermes checkout is available, lock exactly its commit.
# Save the generated reinstall tarball; it contains this hermes.lock.
if [[ ! -f "$LOCK" && -d "$HERMES_LOCAL/.git" ]]; then
  git -C "$HERMES_LOCAL" rev-parse --verify HEAD > "$LOCK"
  info "Locked existing Hermes checkout: $(cat "$LOCK")"
  installed_ver="$(pacman -Q hermes-kms-dkms-git 2>/dev/null | awk '{print $2}' || true)"
  short_sha="$(git -C "$HERMES_LOCAL" rev-parse --short HEAD)"
  if [[ -n "$installed_ver" && "$installed_ver" != *".g${short_sha}-"* ]]; then
    info "WARNING: installed Hermes package ($installed_ver) may not match local checkout ($short_sha)."
  fi
fi

if [[ ! -r /usr/include/drm/hermes_kms_drm.h ]] || ! modinfo hermes_kms >/dev/null 2>&1; then
  info 'Installing Hermes-KMS DKMS (one-time, requires GitHub)'
  if [[ ! -d "$HERMES_CACHE/.git" ]]; then
    mkdir -p "$(dirname "$HERMES_CACHE")"
    git clone "$HERMES_REPO_URL" "$HERMES_CACHE"
  else
    git -C "$HERMES_CACHE" fetch origin
  fi
  if [[ -f "$LOCK" ]]; then
    ref="$(tr -d '\r\n' < "$LOCK")"
    [[ "$ref" =~ ^[a-fA-F0-9]{40}$ ]] || fail "Invalid Hermes commit in $LOCK"
    git -C "$HERMES_CACHE" checkout --detach "$ref"
  else
    git -C "$HERMES_CACHE" checkout main
    git -C "$HERMES_CACHE" pull --ff-only origin main
    git -C "$HERMES_CACHE" rev-parse HEAD > "$LOCK"
  fi
  ref="$(tr -d '\r\n' < "$LOCK")"
  work="$(mktemp -d "${TMPDIR:-/tmp}/bosto-hermes-pkg.XXXXXXXX")"
  cp "$HERMES_CACHE/PKGBUILD" "$work/PKGBUILD"
  cp "$HERMES_CACHE"/*.install "$work/"
  # Pin the source used by makepkg; the checkout alone would not pin its VCS source.
  sed -i "s|git+https://github.com/MrOz59/Hermes-KMS.git|git+https://github.com/MrOz59/Hermes-KMS.git#commit=$ref|" "$work/PKGBUILD"
  grep -Fq "git+https://github.com/MrOz59/Hermes-KMS.git#commit=$ref" "$work/PKGBUILD" || fail 'Could not pin Hermes PKGBUILD.'
  (cd "$work" && makepkg -s --noconfirm)
  packages=("$work"/hermes-kms-dkms-git-*.pkg.tar.zst)
  [[ -f "${packages[0]}" ]] || fail 'Hermes DKMS package not built.'
  sudo pacman -U "${packages[0]}"
  info "Hermes pinned to $ref"
else
  info 'Hermes-KMS DKMS already installed; preserving working driver'
  if [[ ! -f "$LOCK" ]]; then
    info 'WARNING: Hermes commit could not be locked (local checkout not found). Keep a copy of the Hermes source/version too.'
  fi
fi

sudo modprobe hermes_kms initial_enabled=0
[[ -r /usr/include/drm/hermes_kms_drm.h ]] || fail 'Hermes UAPI header not installed.'

info 'Building Hermes -> BOSTO bridge'
make -C "$HERE" clean all
python -m py_compile "$HERE/bosto_hermes.py" "$HERE/artinchip_usb.py"

info 'Installing user service and USB permissions'
mkdir -p "$APP" "$UNIT_DIR"
# Do not overwrite the currently running manual bridge in ~/artinchip-linux.
systemctl --user stop bosto-hermes.service 2>/dev/null || true
install -m 0644 "$HERE/bosto_hermes.py" "$HERE/artinchip_usb.py" "$HERE/libhermes_bosto.so" "$HERE/LICENSE-artinchip-linux.txt" "$APP/"
install -m 0755 "$HERE/run.sh" "$APP/run.sh"
install -m 0644 "$HERE/bosto-hermes.service" "$UNIT_DIR/bosto-hermes.service"
sudo install -m 0644 "$HERE/70-bosto-hermes.rules" "$RULE"
sudo udevadm control --reload-rules
systemctl --user daemon-reload
systemctl --user enable bosto-hermes.service

# Back up this source tree with the resolved Hermes commit for later reinstall.
backup="$HOME/bosto-hermes-reinstall.tar.gz"
tar -C "$(dirname "$HERE")" -czf "$backup" "$(basename "$HERE")"
info "Portable reinstall archive: $backup"

# Avoid two programs claiming the same Hermes output simultaneously.
if pgrep -u "$(id -u)" -f '(^|[[:space:]/])bosto_hermes\.py([[:space:]]|$)' >/dev/null; then
  info 'A manual bosto_hermes.py process is running. Stop it with Ctrl+C, then run:'
  printf '  systemctl --user start bosto-hermes.service\n'
else
  systemctl --user start bosto-hermes.service
fi

info 'Done. If USB permissions are denied, reconnect BOSTO once.'
printf '\nStatus: systemctl --user status bosto-hermes\nLogs:   journalctl --user -u bosto-hermes -f\n'
