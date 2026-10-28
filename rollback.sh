#!/usr/bin/env bash
# Restore the exact Python bridge backed up immediately before installing v1.2.
set -Eeuo pipefail
APP="$HOME/.local/share/bosto-hermes"
BACKUP="$APP/bosto_hermes.py.before-v1.2"
[[ -f "$BACKUP" ]] || { echo "No v1.2 backup at $BACKUP" >&2; exit 1; }
systemctl --user stop bosto-hermes.service
install -m 0644 "$BACKUP" "$APP/bosto_hermes.py"
systemctl --user start bosto-hermes.service
printf 'Previous BOSTO bridge restored. Hermes-KMS was not modified.\n'
