#!/usr/bin/env python3
"""BOSTO BT-101A Hermes-KMS -> ArtInChip USB bridge, pipelined v1.2.

One thread continuously captures from Hermes; a second thread JPEG-encodes
and sends only the newest frame. The mailbox holds at most one raw frame.
Owns the Hermes output: do not run hermes-kmsctl hold at the same time.
"""
import argparse
import ctypes
import io
from pathlib import Path
import signal
import sys
import threading
import time

W, H = 1280, 800
ROOT = Path(__file__).resolve().parent


def log(message):
    print(f"[{time.strftime('%H:%M:%S')}] [bosto-hermes] {message}", flush=True)


class LatestFrame:
    """Single-slot mailbox: never queue stale screen images."""

    def __init__(self):
        self._cv = threading.Condition()
        self._item = None
        self._closed = False

    def put(self, raw, timestamp):
        with self._cv:
            if self._closed:
                return False, False
            replaced = self._item is not None
            self._item = (raw, timestamp)
            self._cv.notify()
            return True, replaced

    def take_when_due(self, next_due, stop):
        """At send deadline, take the freshest frame (not the first queued)."""
        with self._cv:
            while not self._closed and not stop.is_set():
                if self._item is None:
                    self._cv.wait(timeout=0.25)
                    continue
                remaining = next_due - time.monotonic()
                if remaining > 0:
                    self._cv.wait(timeout=min(remaining, 0.25))
                    continue
                item, self._item = self._item, None
                return item
            return None

    def close(self):
        with self._cv:
            self._closed = True
            self._item = None
            self._cv.notify_all()


class Counters:
    def __init__(self):
        self._lock = threading.Lock()
        self._values = [0, 0, 0, 0.0, 0.0, 0, 0.0]
        self.error = None

    def capture(self, replaced):
        with self._lock:
            self._values[0] += 1
            self._values[2] += int(replaced)

    def sent(self, jpeg_ms, usb_ms, size, age_ms):
        with self._lock:
            self._values[1] += 1
            self._values[3] += jpeg_ms
            self._values[4] += usb_ms
            self._values[5] += size
            self._values[6] += age_ms

    def fail(self, exc):
        with self._lock:
            self.error = exc

    def snapshot(self):
        with self._lock:
            old = self._values
            self._values = [0, 0, 0, 0.0, 0.0, 0, 0.0]
            return old


def transmit_loop(mailbox, counters, stop, *, width, height, fps, quality,
                  device, media_format, send_frame, preview=False):
    """Dedicated encoder/USB thread. Never touches Hermes or shared C buffers."""
    from PIL import Image

    next_due = 0.0
    frame_id = 0
    first = True
    period = 1.0 / fps
    try:
        while not stop.is_set():
            item = mailbox.take_when_due(next_due, stop)
            if item is None:
                return
            raw, captured_at = item  # immutable bytes, safe after next C grab
            started = time.monotonic()
            next_due = started + period  # pacing starts BEFORE JPEG/USB work

            img = Image.frombytes('RGB', (width, height), raw, 'raw', 'BGRX')
            if preview and first:
                img.save('/tmp/bosto-hermes-preview.png')
                log('Saved /tmp/bosto-hermes-preview.png')
            first = False
            output = io.BytesIO()
            img.save(output, format='JPEG', quality=quality,
                     subsampling=2, optimize=False)
            jpeg = output.getvalue()
            encoded = time.monotonic()
            if device is not None:
                send_frame(device, jpeg, media_format, frame_id)
                frame_id += 1
            finished = time.monotonic()
            counters.sent((encoded - started) * 1000,
                          (finished - encoded) * 1000,
                          len(jpeg), (finished - captured_at) * 1000)
    except Exception as exc:
        counters.fail(exc)
        stop.set()
        mailbox.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fps', type=int, default=60, help='Max sent FPS, 1..60')
    parser.add_argument('--quality', type=int, default=90, help='JPEG quality 1..100')
    parser.add_argument('--no-usb', action='store_true', help='Capture-only diagnostic')
    args = parser.parse_args()
    if not 1 <= args.fps <= 60 or not 1 <= args.quality <= 100:
        parser.error('fps must be 1..60, quality must be 1..100')

    # Import USB dependencies only when the bridge is actually launched.
    from artinchip_usb import find_device, setup_device, get_params, authenticate, send_frame
    import usb.util

    libpath = ROOT / 'libhermes_bosto.so'
    if not libpath.exists():
        raise SystemExit('Missing libhermes_bosto.so. Run: make')
    lib = ctypes.CDLL(str(libpath))
    lib.hb_open.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
    lib.hb_open.restype = ctypes.c_int
    lib.hb_next.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int]
    lib.hb_next.restype = ctypes.c_int
    lib.hb_error.argtypes = []
    lib.hb_error.restype = ctypes.c_char_p
    lib.hb_close.argtypes = []
    lib.hb_close.restype = None

    def error():
        return lib.hb_error().decode(errors='replace')

    device = None
    hermes_open = False
    stop = threading.Event()
    mailbox = LatestFrame()
    counters = Counters()
    worker = None

    def stop_on_sigterm(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        if not args.no_usb:
            log('Connecting BOSTO USB...')
            device = find_device()
            if device is None:
                raise RuntimeError('BOSTO 33c3:0e02 not found')
            setup_device(device)
            dw, dh, media_format, refresh = get_params(device)
            if (dw, dh) != (W, H):
                raise RuntimeError(f'BOSTO reports {dw}x{dh}; expected {W}x{H}')
            if not authenticate(device):
                raise RuntimeError('BOSTO authentication failed')
            log(f'BOSTO ready: {dw}x{dh} @ {refresh}Hz')
        else:
            media_format = 0

        if lib.hb_open(W, H, 60) != 0:
            raise RuntimeError(f'Hermes startup failed: {error()}\n'
                               'Stop hermes-kmsctl hold before starting the bridge.')
        hermes_open = True
        log(error())
        log('Hermes output active; configure it in GNOME Displays if necessary')
        log(f'Pipelined capture/USB; JPEG quality={args.quality}, '
            f'subsampling=4:2:0; limit={args.fps} FPS')

        worker = threading.Thread(
            name='bosto-jpeg-usb', target=transmit_loop, daemon=True,
            args=(mailbox, counters, stop),
            kwargs=dict(width=W, height=H, fps=args.fps, quality=args.quality,
                        device=device, media_format=media_format,
                        send_frame=send_frame, preview=args.no_usb))
        worker.start()

        pixels = ctypes.create_string_buffer(W * H * 4)
        last_stats = time.monotonic()
        while not stop.is_set():
            # ctypes.CDLL releases the GIL while the C capture call waits/copies.
            result = lib.hb_next(pixels, len(pixels), 250)
            if result < 0:
                raise RuntimeError(f'Hermes capture: {error()}')
            if result == 1:
                timestamp = time.monotonic()
                # Make an immutable copy: the next hb_next overwrites pixels.
                accepted, replaced = mailbox.put(pixels.raw, timestamp)
                if accepted:
                    counters.capture(replaced)

            now = time.monotonic()
            if now - last_stats >= 5.0:
                dt = now - last_stats
                captured, sent, replaced, jpeg_ms, usb_ms, size, age_ms = counters.snapshot()
                label = 'USB' if not args.no_usb else 'encoded'
                log(f'{dt:.1f}s: Hermes {captured/dt:.1f} frames/s, '
                    f'{label} {sent/dt:.1f} FPS, replaced {replaced} | '
                    f'JPEG {jpeg_ms/max(sent,1):.1f}ms, '
                    f'USB {usb_ms/max(sent,1):.1f}ms, '
                    f'{size/max(sent,1)/1024:.0f} KiB/frame, '
                    f'pipeline {age_ms/max(sent,1):.1f}ms')
                last_stats = now

        if counters.error is not None:
            raise RuntimeError(f'JPEG/USB worker failed: {counters.error}')
    except KeyboardInterrupt:
        log('Stopped')
    finally:
        stop.set()
        mailbox.close()
        if worker is not None:
            worker.join(timeout=5)
            if worker.is_alive():
                log('WARNING: USB worker did not exit within 5s')
        if hermes_open:
            lib.hb_close()
        if device is not None:
            try:
                usb.util.release_interface(device, 0)
            except Exception:
                pass


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        log(f'ERROR: {exc}')
        sys.exit(1)
