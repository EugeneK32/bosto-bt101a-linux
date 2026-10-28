#!/usr/bin/env bash
set -Eeuo pipefail
[[ "$(id -u)" != 0 ]] || { echo 'Run without sudo.' >&2; exit 1; }
systemctl --user disable --now bosto-hermes.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/bosto-hermes.service"
rm -rf "$HOME/.local/share/bosto-hermes"
systemctl --user daemon-reload
sudo rm -f /etc/udev/rules.d/70-bosto-hermes.rules
sudo udevadm control --reload-rules
printf 'BOSTO bridge removed. Hermes-KMS DKMS is intentionally left installed.\n'
