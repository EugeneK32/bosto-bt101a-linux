#!/usr/bin/env bash
# Update ONLY the Python bridge. Hermes-KMS, its kernel module and USB protocol remain unchanged.
set -Eeuo pipefail
HERE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/.local/share/bosto-hermes"
SERVICE=bosto-hermes.service
BACKUP="$APP/bosto_hermes.py.before-v1.2"
[[ "$(id -u)" != 0 ]] || { echo 'Run without sudo: ./update.sh' >&2; exit 1; }
[[ -f "$APP/bosto_hermes.py" ]] || { echo 'Bridge not installed: run ./install.sh first' >&2; exit 1; }
python - "$HERE/bosto_hermes.py" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
compile(p.read_bytes(), str(p), 'exec')
PY
# Keep the user's current *working* script, even if it was locally modified.
cp -p "$APP/bosto_hermes.py" "$BACKUP"
systemctl --user stop "$SERVICE"
install -m 0644 "$HERE/bosto_hermes.py" "$APP/bosto_hermes.py"
if ! systemctl --user start "$SERVICE"; then
  echo 'New service failed to start; restoring previous script.' >&2
  install -m 0644 "$BACKUP" "$APP/bosto_hermes.py"
  systemctl --user start "$SERVICE" || true
  exit 1
fi
sleep 2
if ! systemctl --user is-active --quiet "$SERVICE"; then
  echo 'New service is not active; restoring previous script.' >&2
  systemctl --user stop "$SERVICE" || true
  install -m 0644 "$BACKUP" "$APP/bosto_hermes.py"
  systemctl --user start "$SERVICE" || true
  exit 1
fi
# Carry over the pinned Hermes commit from a previous portable backup.
if [[ ! -f "$HERE/hermes.lock" ]]; then
  for old in "$HOME/bosto-hermes-reinstall-v1.1.tar.gz" "$HOME/bosto-hermes-reinstall.tar.gz"; do
    [[ -f "$old" ]] || continue
    path="$(tar -tzf "$old" 2>/dev/null | grep -m1 -E '(^|/)hermes\.lock$' || true)"
    if [[ -n "$path" ]]; then
      tar -xOzf "$old" "$path" > "$HERE/hermes.lock"
      echo "Recovered Hermes pin from $old"
      break
    fi
  done
fi
if [[ ! -f "$HERE/hermes.lock" ]]; then
  echo 'WARNING: hermes.lock not found. Keep a copy of your installed Hermes-KMS commit.' >&2
fi
NEW_BACKUP="$HOME/bosto-hermes-reinstall-v1.2.tar.gz"
tar -C "$(dirname "$HERE")" -czf "$NEW_BACKUP" "$(basename "$HERE")"
printf '\nBOSTO v1.2 pipeline installed.\n'
printf 'Previous Python bridge: %s\n' "$BACKUP"
printf 'Rollback: %s/rollback.sh\n' "$HERE"
printf 'Reinstall archive: %s\n' "$NEW_BACKUP"
printf 'Logs: journalctl --user -u bosto-hermes -f\n'
