"""
File:        test_live_inference.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.1.0
Change:      0.1.0 (training-live-inference-handoff.md, request G): new file.
Description: Unit tests for the Live Inference tab's display logic
             (live_inference_widget.InferenceView, no Qt window): the status
             card's states and colors, the bars, smoothing, the display cutoff
             taken from the board's det_conf once per connection, the recent
             detections (newest first, the last 10), Reset counters, the
             inference rate, NO DATA after 2 s, model faults, and the stats
             line (a failed self-test in red). Also LiveBuffer's channel list.
             Run from cnn_record_gui/: python -m unittest discover -s tests
"""

# ============================================================================
# Imports
# ============================================================================

import unittest

import helpers  # Sets sys.path; must come before the GUI imports
import cnn_config as cfg
from app_styles import AppStyles
from live_buffer import LiveBuffer
from live_inference_widget import ARMED, ERROR, IDLE, OK, WARN, InferenceView
from snapshot_protocol import DetLine, LineParser, ProbLine, SampleLine

# ============================================================================
# Functions
# ============================================================================


def prob(n, p, lat=21000):
    """@return A ProbLine with probabilities p."""
    return ProbLine(n, tuple(p), lat, "")


def connected_view():
    """@return A connected InferenceView with the board's settings applied."""
    v = InferenceView()
    v.connected = True
    v.apply_settings(LineParser().feed(helpers.BOOT_INF).settings)
    return v

# ============================================================================
# Tests
# ============================================================================


class TestCard(unittest.TestCase):
    def test_not_connected_then_waiting(self):
        v = InferenceView()
        self.assertEqual(v.card(0.0).headline, "NOT CONNECTED")
        v.connected = True
        self.assertEqual((v.card(0.0).state, v.card(0.0).headline), (IDLE, "WAITING FOR STREAM"))

    def test_colors_follow_the_board_rule(self):
        v = connected_view()
        v.add_prob(prob(1, (0.85, 0.10, 0.05)), wall=10.0)
        c = v.card(10.5)
        self.assertEqual((c.state, c.headline), (OK, "POTHOLE"))            # Green
        v.add_prob(prob(2, (0.45, 0.20, 0.35)), wall=11.0)
        c = v.card(11.0)
        self.assertEqual((c.state, c.headline), (WARN, "POTHOLE"))          # Amber: unsure
        self.assertIn("unsure", c.subline)
        v.add_prob(prob(3, (0.02, 0.03, 0.95)), wall=12.0)
        self.assertEqual(v.card(12.0).state, ARMED)                         # Blue: NONE
        self.assertEqual([s for _n, _p, s in v.bars()], [WARN, WARN, ARMED])

    def test_no_data_after_two_seconds(self):
        v = connected_view()
        v.add_prob(prob(1, (0.1, 0.1, 0.8)), wall=5.0)
        self.assertNotEqual(v.card(5.0 + cfg.LIVE_INF_STALE_S - 0.1).headline, "NO DATA")
        self.assertEqual(v.card(5.0 + cfg.LIVE_INF_STALE_S).headline, "NO DATA")

    def test_model_fault(self):
        v = connected_view()
        v.model_fault = ("F8", "SELFTEST_FAILED")
        c = v.card(0.0)
        self.assertEqual((c.state, c.headline), (ERROR, "MODEL FAULT F8"))
        self.assertIn("recording go on", c.subline)


class TestControls(unittest.TestCase):
    def test_cutoff_from_board_once_per_connection(self):
        v = InferenceView()
        settings = LineParser().feed(helpers.BOOT_INF).settings
        self.assertEqual(v.apply_settings(settings), 0.60)
        v.cutoff = 0.80                                      # The user moved it
        self.assertIsNone(v.apply_settings(settings))        # A later CFG keeps it
        self.assertEqual(v.cutoff, 0.80)
        v.new_connection()
        self.assertEqual(v.apply_settings(settings), 0.60)   # A new connection takes it again

    def test_cutoff_changes_only_the_display(self):
        v = connected_view()
        v.add_prob(prob(1, (0.70, 0.20, 0.10)), wall=1.0)
        self.assertEqual(v.card(1.0).state, OK)
        v.cutoff = 0.75
        self.assertEqual(v.card(1.0).state, WARN)
        self.assertEqual(v.board_cutoff, 0.60)               # The board's stays as sent

    def test_smoothing(self):
        v = connected_view()
        v.smooth = 0.5
        v.add_prob(prob(1, (1.0, 0.0, 0.0)), wall=1.0)
        v.add_prob(prob(2, (0.0, 0.0, 1.0)), wall=1.5)
        self.assertEqual([round(p, 3) for _n, p, _s in v.bars()], [0.5, 0.0, 0.5])
        v.smooth = 0.0                                       # Off: the latest as sent
        v.add_prob(prob(3, (0.0, 1.0, 0.0)), wall=2.0)
        self.assertEqual([p for _n, p, _s in v.bars()], [0.0, 1.0, 0.0])

    def test_recent_newest_first_and_capped(self):
        v = connected_view()
        for i in range(cfg.LIVE_INF_RECENT_N + 3):
            v.add_det(DetLine(1000 + i, "POTHOLE", 0.9, ""), "12:00:%02d" % i)
        self.assertEqual(len(v.recent), cfg.LIVE_INF_RECENT_N)
        self.assertEqual(v.recent[0][3], 1000 + cfg.LIVE_INF_RECENT_N + 2)

    def test_reset_counters(self):
        v = connected_view()
        for i in range(5):
            v.add_prob(prob(208 * i, (0.1, 0.1, 0.8), lat=20000 + i), wall=float(i))
        v.add_det(DetLine(5, "POTHOLE", 0.9, ""), "12:00:00")
        self.assertAlmostEqual(v.rate(416), 2.0)
        self.assertEqual((v.count, v.lat_max_us), (5, 20004))
        v.reset_counters()
        self.assertEqual((v.count, v.lat_max_us, len(v.recent)), (0, None, 0))
        self.assertIsNone(v.rate(416))


class TestStats(unittest.TestCase):
    def test_selftest_fail_in_red(self):
        v = connected_view()
        self.assertNotIn(AppStyles.ACCENT_ERROR, v.stats_html(0, 416))
        v.selftest = "fail"
        self.assertIn(AppStyles.ACCENT_ERROR, v.stats_html(0, 416))

    def test_board_counters_and_malformed(self):
        v = connected_view()
        v.board = {"inferences": 120, "inf_skips": 2}
        text = v.stats_html(3, 416)
        for part in ("inferences 120", "skips 2", "malformed 3", "synthetic-20261124-130048"):
            self.assertIn(part, text)


class TestLiveBufferChannels(unittest.TestCase):
    def test_model_channels(self):
        buf = LiveBuffer(10.0, 0.2, channels=("z_hp", "roll", "pitch"))
        buf.add(SampleLine(8, 19231, (0.1, 2.0, -3.0)))
        _t, cols = buf.series()
        self.assertEqual(sorted(cols), ["pitch", "roll", "z_hp"])
        self.assertEqual(cols["pitch"], [-3.0])


if __name__ == "__main__":
    unittest.main()
