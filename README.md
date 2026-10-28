# BOSTO BT-101A on Linux

[Русский](README.ru.md)

Linux display bridge for the **BOSTO BT-101A** USB monitor, powered by [Hermes-KMS](https://github.com/MrOz59/Hermes-KMS).

- **Platform:** Arch Linux, GNOME Wayland
- **Device:** `33c3:0e02` (ArtInChip)
- **Display:** 1280 × 800 @ 60 Hz
- **Encoding:** JPEG quality 90, 4:2:0
- **Pipeline:** GNOME → Hermes-KMS → DMA-BUF → JPEG → USB

Frames are captured and transmitted in separate threads. A single-frame buffer keeps the latest frame and avoids building up latency.

## Installation

Requires a working GNOME Wayland session and GPU driver. From the project directory:

```bash
./install.sh
```

The installer installs required Arch packages (including kernel headers and DKMS), installs Hermes-KMS if needed, builds the bridge, configures USB permissions, and enables the user systemd service.

**Reconnect the USB display once** after the first installation. Do not run `hermes-kmsctl hold` alongside the bridge.

## Configuration

Optional: create `~/.config/bosto-hermes.conf`:

```ini
BOSTO_FPS=60
BOSTO_QUALITY=90
```

Apply changes:

```bash
systemctl --user restart bosto-hermes
```

## Service and logs

```bash
systemctl --user status bosto-hermes
systemctl --user restart bosto-hermes
systemctl --user stop bosto-hermes
journalctl --user -u bosto-hermes -f
```

`USB ... FPS` shows transmitted frames per second. `replaced` counts outdated frames replaced before transmission, not USB errors.

The service waits for the USB display and retries after disconnection.

## Update, backup, removal

```bash
./update.sh      # Update an existing installation; back up the previous bridge
./rollback.sh    # Restore the previous bridge after an update
./uninstall.sh   # Remove the bridge and service; leave Hermes-KMS installed
```

The installer creates `~/bosto-hermes-reinstall.tar.gz` for future reinstalls. Keep a copy. When available, `hermes.lock` pins the Hermes-KMS commit.

**Scope:** display output only; pen and touch input are not configured.

## Credits

- [Hermes-KMS](https://github.com/MrOz59/Hermes-KMS) — virtual DRM display
- [artinchip-linux](https://github.com/hevnsnt/artinchip-linux) — ArtInChip USB protocol (MIT; license included)
