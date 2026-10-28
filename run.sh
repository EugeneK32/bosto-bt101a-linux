#!/usr/bin/env bash
# Runs only while the BOSTO USB device exists; reconnects automatically.
set -u
APP_DIR="$HOME/.local/share/bosto-hermes"

usb_connected() {
  local d vendor product
  for d in /sys/bus/usb/devices/*; do
    [[ -f "$d/idVendor" && -f "$d/idProduct" ]] || continue
    read -r vendor < "$d/idVendor" || continue
    read -r product < "$d/idProduct" || continue
    if [[ "$vendor" == 33c3 && "$product" == 0e02 ]]; then
      return 0
    fi
  done
  return 1
}

while true; do
  if usb_connected; then
    echo '[bosto-hermes] USB detected, starting bridge'
    /usr/bin/python -u "$APP_DIR/bosto_hermes.py" \
      --fps "${BOSTO_FPS:-60}" --quality "${BOSTO_QUALITY:-90}"
    rc=$?
    echo "[bosto-hermes] Bridge exited ($rc), retrying in 3s"
    sleep 3
  else
    sleep 2
  fi
done
