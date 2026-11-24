"""
File:        test_evaluate.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.1.0
Change:      0.1.0 (training-live-inference-handoff.md, request E): new file.
Description: Unit tests for evaluate.py: the board's sliding window (window
             ends, damaged windows skipped), the detection rule (cutoff, NONE
             ignored, the 2 s lockout counted in samples, ties), event outcomes,
             the cutoff table with false alarms per hour, and window scores.
             NumPy only (no TensorFlow).
"""

# ============================================================================
# Imports
# ============================================================================

import unittest

import numpy as np

import helpers  # noqa: F401  (sets sys.path; must come first)
import cnn_config as cfg
import evaluate as ev

# ============================================================================
# Constants
# ============================================================================

P, B, N = 0, 1, 2            # Class indexes: POTHOLE, SPEED_BUMP, NONE
CLASSES = ("POTHOLE", "SPEED_BUMP", "NONE")
RATE = 416
WIDTH = 832
HOP = 208
LOCK = 2.0                   # DET_LOCKOUT_S (owner, 2026-11-24)

# ============================================================================
# Functions
# ============================================================================


def probs_for(seq) -> np.ndarray:
    """
    @param seq  (class index, top probability) per window.
    @return (windows, 3) probabilities with the rest split evenly.
    """
    out = []
    for k, conf in seq:
        p = np.full(3, (1.0 - conf) / 2.0)
        p[k] = conf
        out.append(p)
    return np.array(out, dtype=np.float32)


def scanned(label, ends, seq, duration_s=6.0) -> ev.ScannedClip:
    """@return A ScannedClip with the given window ends and outputs."""
    return ev.ScannedClip(clip_id="s/%s" % label, label=label, duration_s=duration_s,
                          ends=np.array(ends, dtype=np.int64), probs=probs_for(seq),
                          skipped=0, rate_hz=RATE)

# ============================================================================
# Classes
# ============================================================================


class TestDetections(unittest.TestCase):
    """The board's rule: top class not NONE, at least the cutoff, 2 s lockout."""

    def test_cutoff_and_none(self):
        ends = 831 + HOP * np.arange(3)
        dets = ev.detections(ends, probs_for([(N, 0.9), (P, 0.55), (P, 0.65)]), RATE, 0.6,
                             LOCK, N)
        self.assertEqual([(int(ends[2]), P)], [(e, k) for e, k, _ in dets])

    def test_at_cutoff_counts(self):
        dets = ev.detections(np.array([831]), probs_for([(B, 0.6)]), RATE, 0.6, LOCK, N)
        self.assertEqual(1, len(dets))

    def test_lockout_two_seconds(self):
        # A window every 0.5 s, all POTHOLE: one detection every 2.0 s (832 rows).
        ends = 831 + HOP * np.arange(9)
        dets = ev.detections(ends, probs_for([(P, 0.9)] * 9), RATE, 0.6, LOCK, N)
        self.assertEqual([int(ends[0]), int(ends[4]), int(ends[8])], [e for e, _, _ in dets])

    def test_lockout_covers_both_classes(self):
        ends = 831 + HOP * np.arange(3)
        dets = ev.detections(ends, probs_for([(P, 0.9), (B, 0.9), (B, 0.9)]), RATE, 0.6,
                             LOCK, N)
        self.assertEqual([P], [k for _, k, _ in dets])

    def test_lockout_in_samples_across_skipped_windows(self):
        self.assertEqual(2, len(ev.detections(np.array([831, 1663]), probs_for([(P, 0.9)] * 2),
                                              RATE, 0.6, LOCK, N)))
        self.assertEqual(1, len(ev.detections(np.array([831, 1662]), probs_for([(P, 0.9)] * 2),
                                              RATE, 0.6, LOCK, N)))

    def test_tie_goes_to_first_class(self):
        probs = np.array([[0.45, 0.45, 0.10]], dtype=np.float32)
        dets = ev.detections(np.array([831]), probs, RATE, 0.4, LOCK, N)
        self.assertEqual(P, dets[0][1])


class TestOutcome(unittest.TestCase):
    """One event clip's outcome."""

    def test_outcomes(self):
        self.assertEqual(ev.MISSED, ev.event_outcome([], P))
        self.assertEqual(ev.ONCE, ev.event_outcome([(1, P, 0.9)], P))
        self.assertEqual(ev.DOUBLE, ev.event_outcome([(1, P, 0.9), (900, P, 0.9)], P))
        self.assertEqual(ev.WRONG, ev.event_outcome([(1, B, 0.9)], P))
        self.assertEqual(ev.WRONG, ev.event_outcome([(1, P, 0.9), (900, B, 0.9)], P))


class TestScan(unittest.TestCase):
    """The board's windows over a whole clip."""

    def test_window_ends_of_a_manual_clip(self):
        starts, skipped = ev.scan_starts(2496, None, None, WIDTH, HOP)
        self.assertEqual([831 + HOP * k for k in range(9)], (starts + WIDTH - 1).tolist())
        self.assertEqual(0, skipped)

    def test_clip_shorter_than_a_window(self):
        starts, skipped = ev.scan_starts(800, None, None, WIDTH, HOP)
        self.assertEqual((0, 0), (len(starts), skipped))

    def test_ok0_windows_skipped(self):
        n = np.arange(2496)
        ok = np.ones(2496, dtype=bool)
        ok[1000] = False
        starts, skipped = ev.scan_starts(2496, n, ok, WIDTH, HOP)
        self.assertEqual(4, skipped)          # Starts 208, 416, 624 and 832 hold row 1000
        self.assertTrue(all(not s <= 1000 < s + WIDTH for s in starts))

    def test_gap_windows_skipped(self):
        n = np.arange(2496)
        n[1500:] += 20                        # 20 samples lost before row 1500
        starts, skipped = ev.scan_starts(2496, n, None, WIDTH, HOP)
        self.assertGreater(skipped, 0)
        self.assertTrue(all(not s < 1500 <= s + WIDTH - 1 for s in starts))

    def test_scan_clips_chunks_and_order(self):
        # The model "answers" with the window's first z_hp value as a class.
        def clip(cid, label, k, rows):
            model = np.zeros((rows, 3))
            model[:, 0] = k
            return ev.ClipData(cid, label, model, None, None, RATE)

        def predict(x):
            out = np.zeros((len(x), 3), dtype=np.float32)
            out[np.arange(len(x)), x[:, 0, 0].astype(int)] = 1.0
            return out

        clips = [clip("s/a", "POTHOLE", P, 2496), clip("s/b", "NONE", N, 1500),
                 clip("s/c", "SPEED_BUMP", B, 2496)]
        old = ev.PREDICT_CHUNK
        ev.PREDICT_CHUNK = 4                  # Force several chunks
        try:
            out = ev.scan_clips(clips, predict, WIDTH, HOP, 3)
        finally:
            ev.PREDICT_CHUNK = old
        self.assertEqual([9, 4, 9], [len(c.ends) for c in out])
        for c, k in zip(out, (P, N, B)):
            self.assertTrue(np.all(np.argmax(c.probs, 1) == k))
        self.assertAlmostEqual(1500 / RATE, out[1].duration_s)


class TestCutoffTable(unittest.TestCase):
    """Streaming results per cutoff."""

    def setUp(self):
        self.clips = [
            scanned("POTHOLE", [831], [(P, 0.7)]),                       # once up to 0.70
            scanned("SPEED_BUMP", [831, 1663], [(B, 0.9), (B, 0.9)]),    # double up to 0.90
            scanned("NONE", [831], [(P, 0.8)], duration_s=36.0),         # 1 false alarm in 0.01 h
        ]

    def test_rates_and_false_alarms(self):
        rows = ev.cutoff_table(self.clips, CLASSES, "NONE", (0.5, 0.75, 0.95), LOCK)
        low, mid, high = rows
        self.assertEqual((1, 1, 0, 0), tuple(low.outcomes[o] for o in
                                             (ev.ONCE, ev.DOUBLE, ev.WRONG, ev.MISSED)))
        self.assertAlmostEqual(100.0, low.fa_per_hour)
        self.assertEqual(1, mid.outcomes[ev.MISSED])
        self.assertEqual(1, mid.per_class["POTHOLE"][ev.MISSED])
        self.assertEqual(1, mid.false_alarms)                # 0.8 >= 0.75
        self.assertEqual(2, high.outcomes[ev.MISSED])
        self.assertEqual(0, high.false_alarms)
        self.assertEqual(0.0, high.fa_per_hour)

    def test_false_alarms_missing_without_none_clips(self):
        rows = ev.cutoff_table(self.clips[:2], CLASSES, "NONE", (0.5,), LOCK)
        self.assertIsNone(rows[0].fa_per_hour)
        self.assertIn("missing", ev.format_cutoff_table(rows, ("POTHOLE", "SPEED_BUMP"))[1])

    def test_clip_without_windows_left_out(self):
        rows = ev.cutoff_table(self.clips + [scanned("POTHOLE", [], [])], CLASSES, "NONE",
                               (0.5,), LOCK)
        self.assertEqual(2, rows[0].events)

    def test_config_cutoffs(self):
        self.assertEqual(10, len(cfg.CUTOFFS))
        self.assertAlmostEqual(0.50, cfg.CUTOFFS[0])
        self.assertAlmostEqual(0.95, cfg.CUTOFFS[-1])
        self.assertEqual(2.0, cfg.DET_LOCKOUT_S)


class TestWindowScores(unittest.TestCase):
    """Confusion matrix and recall."""

    def test_matrix_and_recall(self):
        ws = ev.window_scores(np.array([P, P, N]), probs_for([(P, 0.9), (N, 0.9), (N, 0.9)]), 3)
        self.assertEqual([[1, 0, 1], [0, 0, 0], [0, 0, 1]], ws.matrix.tolist())
        self.assertEqual([0.5, None, 1.0], ws.recall)
        lines = ev.format_scores(ws, CLASSES)
        self.assertTrue(any("missing" in s and "SPEED_BUMP" in s for s in lines))


if __name__ == "__main__":
    unittest.main()
