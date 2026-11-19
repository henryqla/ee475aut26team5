"""
File:        test_live_buffer.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Description: Unit tests for live_buffer.py (the live IMU view's data): the
             time window, starting over when device time goes backwards, the
             break drawn where S lines are missing, and the line rate.
"""

# ============================================================================
# Imports
# ============================================================================

import math
import unittest

import helpers  # Sets sys.path; must come before the GUI imports
from live_buffer import LiveBuffer
from snapshot_protocol import S_CHANNELS, SampleLine

# ============================================================================
# Functions
# ============================================================================

PERIOD_US = 8 * 1_000_000 // 416   # One S line per 8 samples at 416 Hz (19230 us)


def sample(k, t0_us=0, z=0.0):
    """
    @param k      Index of the S line (n = 8 * k).
    @param t0_us  Device time of line 0.
    @param z      z_hp value, to tell samples apart.
    @return A SampleLine as the parser makes it.
    """
    return SampleLine(8 * k, t0_us + k * PERIOD_US, (0.0, 0.0, 1.0, 0.4, -0.3, 0.2, z))

# ============================================================================
# Tests
# ============================================================================


class TestLiveBuffer(unittest.TestCase):
    def test_keeps_only_the_window(self):
        buf = LiveBuffer(window_s=1.0, gap_s=0.2)
        for k in range(200):                      # ~3.8 s of lines
            buf.add(sample(k))
        t, _ = buf.series()
        self.assertEqual(t[-1], 0.0)              # Newest is "now"
        self.assertGreaterEqual(t[0], -1.0)
        self.assertLess(t[0], -1.0 + PERIOD_US / 1e6)
        self.assertEqual(len(buf), len(t))        # No gaps, so no NaN points

    def test_starts_over_when_device_time_goes_back(self):
        buf = LiveBuffer(window_s=10.0, gap_s=0.2)
        for k in range(50):
            self.assertFalse(buf.add(sample(k, t0_us=5_000_000)))
        self.assertTrue(buf.add(sample(0, t0_us=1000)))   # Board reset
        self.assertEqual(len(buf), 1)
        self.assertEqual(buf.restarts, 1)
        self.assertEqual(buf.latest().t_us, 1000)

    def test_repeated_time_starts_over(self):
        buf = LiveBuffer(window_s=10.0, gap_s=0.2)
        buf.add(sample(3))
        self.assertTrue(buf.add(sample(3)))

    def test_gap_gets_one_nan_point(self):
        buf = LiveBuffer(window_s=10.0, gap_s=0.2)
        for k in list(range(10)) + list(range(30, 40)):   # 20 lines missing (~0.4 s)
            buf.add(sample(k, z=k))
        t, cols = buf.series()
        self.assertEqual(len(t), 21)
        self.assertTrue(math.isnan(t[10]))
        for name in S_CHANNELS:
            self.assertEqual(len(cols[name]), 21)
            self.assertTrue(math.isnan(cols[name][10]))
        self.assertEqual(cols["z_hp"][9], 9)
        self.assertEqual(cols["z_hp"][11], 30)

    def test_short_gap_is_not_a_break(self):
        buf = LiveBuffer(window_s=10.0, gap_s=0.2)
        for k in (0, 1, 2, 5, 6):                 # 2 lines missing (~0.06 s)
            buf.add(sample(k))
        t, _ = buf.series()
        self.assertFalse(any(math.isnan(x) for x in t))

    def test_rate(self):
        buf = LiveBuffer(window_s=10.0, gap_s=0.2)
        self.assertIsNone(buf.rate_hz())
        buf.add(sample(0))
        self.assertIsNone(buf.rate_hz())
        for k in range(1, 200):
            buf.add(sample(k))
        self.assertAlmostEqual(buf.rate_hz(), 1e6 / PERIOD_US, places=3)  # ~52 lines/s

    def test_clear(self):
        buf = LiveBuffer(window_s=10.0, gap_s=0.2)
        buf.add(sample(5))
        buf.add(sample(0))                        # One restart
        buf.clear()
        self.assertEqual(len(buf), 0)
        self.assertEqual(buf.restarts, 0)
        self.assertIsNone(buf.latest())
        self.assertEqual(buf.series(), ([], {name: [] for name in S_CHANNELS}))

    def test_max_points(self):
        buf = LiveBuffer(window_s=1000.0, gap_s=0.2, max_points=100)
        for k in range(500):
            buf.add(sample(k))
        self.assertEqual(len(buf), 100)
        self.assertEqual(buf.latest().n, 8 * 499)


if __name__ == "__main__":
    unittest.main()
