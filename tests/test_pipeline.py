"""Offline tests: no Hermes module or BOSTO USB hardware required."""
import importlib.util
from pathlib import Path
import threading
import time
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'bosto_hermes.py'
spec = importlib.util.spec_from_file_location('bosto_hermes', MODULE)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def bgra_frame(width, height, r=25, g=60, b=90):
    return bytes((b, g, r, 0)) * (width * height)


class MailboxTests(unittest.TestCase):
    def test_newest_replaces_old(self):
        box = bridge.LatestFrame()
        stop = threading.Event()
        self.assertEqual(box.put(b'old', 1.0), (True, False))
        self.assertEqual(box.put(b'new', 2.0), (True, True))
        self.assertEqual(box.take_when_due(0, stop), (b'new', 2.0))
        box.close()
        self.assertIsNone(box.take_when_due(0, stop))

    def test_close_wakes_waiter(self):
        box = bridge.LatestFrame()
        stop = threading.Event()
        results = []
        t = threading.Thread(target=lambda: results.append(box.take_when_due(0, stop)))
        t.start()
        box.close()
        t.join(timeout=1)
        self.assertFalse(t.is_alive())
        self.assertEqual(results, [None])

    def test_due_uses_freshest_frame(self):
        box = bridge.LatestFrame()
        stop = threading.Event()
        results = []
        due = time.monotonic() + 0.06
        box.put(b'first', due - 0.1)
        t = threading.Thread(target=lambda: results.append(box.take_when_due(due, stop)))
        t.start()
        time.sleep(0.01)
        box.put(b'latest', due - 0.05)
        t.join(timeout=1)
        self.assertFalse(t.is_alive())
        self.assertEqual(results[0][0], b'latest')


class WorkerTests(unittest.TestCase):
    def test_worker_jpeg_usb_and_pacing(self):
        box = bridge.LatestFrame()
        stop = threading.Event()
        counters = bridge.Counters()
        calls = []
        def fake_send(device, jpeg, fmt, frame_id):
            calls.append((time.monotonic(), frame_id, jpeg))
        t = threading.Thread(target=bridge.transmit_loop, args=(box, counters, stop),
                             kwargs=dict(width=16, height=12, fps=25, quality=90,
                                         device=object(), media_format=5,
                                         send_frame=fake_send))
        t.start()
        # Produce faster than worker, forcing replacements without a backlog.
        for _ in range(60):
            box.put(bgra_frame(16, 12), time.monotonic())
            time.sleep(0.004)
        time.sleep(0.09)
        stop.set()
        box.close()
        t.join(timeout=1)
        self.assertFalse(t.is_alive())
        self.assertIsNone(counters.error)
        self.assertGreaterEqual(len(calls), 4)
        self.assertLessEqual(len(calls), 9)
        self.assertEqual([c[1] for c in calls], list(range(len(calls))))
        self.assertTrue(all(c[2].startswith(b'\xff\xd8') for c in calls))
        gaps = [b[0]-a[0] for a,b in zip(calls, calls[1:])]
        self.assertTrue(all(g >= 0.033 for g in gaps), gaps)
        self.assertEqual(counters.snapshot()[1], len(calls))

    def test_usb_error_stops_capture(self):
        box = bridge.LatestFrame()
        stop = threading.Event()
        counters = bridge.Counters()
        def bad_send(*args):
            raise OSError('simulated USB unplug')
        t = threading.Thread(target=bridge.transmit_loop, args=(box, counters, stop),
                             kwargs=dict(width=8, height=8, fps=60, quality=90,
                                         device=object(), media_format=5,
                                         send_frame=bad_send))
        t.start()
        box.put(bgra_frame(8, 8), time.monotonic())
        t.join(timeout=1)
        self.assertFalse(t.is_alive())
        self.assertTrue(stop.is_set())
        self.assertIsInstance(counters.error, OSError)

    def test_no_usb_still_encodes(self):
        box = bridge.LatestFrame()
        stop = threading.Event()
        counters = bridge.Counters()
        t = threading.Thread(target=bridge.transmit_loop, args=(box, counters, stop),
                             kwargs=dict(width=8, height=8, fps=60, quality=90,
                                         device=None, media_format=0,
                                         send_frame=lambda *args: self.fail('should not send')))
        t.start()
        box.put(bgra_frame(8, 8), time.monotonic())
        time.sleep(0.06)
        stop.set()
        box.close()
        t.join(timeout=1)
        self.assertIsNone(counters.error)
        self.assertEqual(counters.snapshot()[1], 1)


if __name__ == '__main__':
    unittest.main()
