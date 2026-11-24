"""
File:        test_windows.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.0
Change:      0.1.0 (manual-recording-overhaul-handoff.md, request C): new file.
Description: Unit tests for windows.py on synthetic clips, the automatic part
             of WIN-1 to WIN-4: the same arrays from two runs; the peak found
             within 25 ms of a known bump; 5 windows per event clip with the
             peak between 20% and 80%; NONE windows every 1 s; a bump near the
             start, a clip shorter than the window and an older 624-row take;
             windows across a gap, a reset or ok=0 rows skipped and listed;
             empty and unparseable files listed; the minimum warning; no clip
             file changed (checked by hash); decimation.
             Run from training/: python -m unittest discover -s tests
"""

# ============================================================================
# Imports
# ============================================================================

import csv
import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np

import helpers  # Sets sys.path; must come first
import cnn_config as cfg
import windows as wn
from clip_file import load_clip

# ============================================================================
# Constants
# ============================================================================

RATE = helpers.RATE
WIDTH = int(round(cfg.WINDOW_S * RATE))           # 832
CLIP_ROWS = 2496                                   # 1 s + 5 s
TOL_ROWS = int(0.025 * RATE)                       # 25 ms = 10 rows

# ============================================================================
# Functions
# ============================================================================


def by_file(ws):
    """@return The WindowSet's report rows by file name."""
    return {r.file: r for r in ws.report}


def file_hashes(root):
    """@return {path: sha256} of every file under root."""
    return {p: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(root).rglob("*")) if p.is_file()}

# ============================================================================
# Tests
# ============================================================================


class TestWindows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name)
        self.s1 = self.data / "2026-11-24_bench"

    def tearDown(self):
        self.tmp.cleanup()

    def test_peak_within_25_ms(self):
        for k, at in enumerate((300, 700, 1200, 1800, 2300)):
            path = helpers.write_clip(self.s1, "POTHOLE-%d.csv" % (k + 1),
                                      helpers.records(CLIP_ROWS, bump_at=at, seed=k))
            clip = load_clip(path)
            peak = wn.find_peak(clip.model, clip.ok, clip.rate_hz, cfg.PEAK_SMOOTH_S)
            self.assertLessEqual(abs(peak - at), TOL_ROWS, (at, peak))

    def test_five_windows_peak_between_20_and_80(self):
        helpers.write_clip(self.s1, "SPEED_BUMP-1.csv", helpers.records(CLIP_ROWS, bump_at=1100))
        ws = wn.build_windows(self.data)
        self.assertEqual(ws.X.shape, (5, WIDTH, 3))
        self.assertEqual(ws.X.dtype, np.float32)
        self.assertEqual(list(ws.y), [cfg.CLASSES.index("SPEED_BUMP")] * 5)
        rep = by_file(ws)["SPEED_BUMP-1.csv"]
        self.assertAlmostEqual(rep.peak_s, 1100 / RATE, delta=TOL_ROWS / RATE)
        starts = wn._event_starts(1100, CLIP_ROWS, WIDTH, wn.WindowOptions(), "x/y")
        self.assertEqual(len(set(starts.tolist())), 5)                   # All different
        for s in starts:
            self.assertTrue(0.2 * WIDTH <= 1100 - s <= 0.8 * WIDTH)
            self.assertTrue(0 <= s <= CLIP_ROWS - WIDTH)
        # Each window holds the bump between 20% and 80% of its length: its
        # largest |z_hp| sits there.
        for w in ws.X:
            pos = int(np.argmax(np.abs(w[:, 0])))
            self.assertTrue(0.2 * WIDTH - TOL_ROWS <= pos <= 0.8 * WIDTH + TOL_ROWS, pos)

    def test_windows_are_the_clip_rows(self):
        helpers.write_clip(self.s1, "NONE-1.csv", helpers.records(CLIP_ROWS))
        ws = wn.build_windows(self.data)
        clip = load_clip(self.s1 / "NONE-1.csv")
        hop = int(round(cfg.NONE_HOP_S * RATE))                         # 416 = 1 s
        self.assertEqual(len(ws.y), 5)                                   # 0, 1, 2, 3, 4 s
        for k in range(5):
            np.testing.assert_array_equal(ws.X[k], clip.model[k * hop:k * hop + WIDTH]
                                          .astype(np.float32))

    def test_long_none_every_second(self):
        helpers.write_clip(self.s1, "NONE-1.csv", helpers.records(10 * RATE),
                           source=cfg.SOURCE_LONG)
        ws = wn.build_windows(self.data)
        self.assertEqual(len(ws.y), (10 * RATE - WIDTH) // RATE + 1)    # 9
        self.assertTrue(all(c == "2026-11-24_bench/NONE-1.csv" for c in ws.clip_ids))

    def test_same_arrays_every_run(self):
        for k in range(3):
            helpers.write_clip(self.s1, "POTHOLE-%d.csv" % (k + 1),
                               helpers.records(CLIP_ROWS, bump_at=900 + 100 * k, seed=k))
        helpers.write_clip(self.s1, "NONE-1.csv", helpers.records(CLIP_ROWS, seed=9))
        a = wn.build_windows(self.data)
        b = wn.build_windows(self.data)
        np.testing.assert_array_equal(a.X, b.X)
        np.testing.assert_array_equal(a.y, b.y)
        np.testing.assert_array_equal(a.sessions, b.sessions)
        np.testing.assert_array_equal(a.clip_ids, b.clip_ids)
        # A clip in another session does not change these clips' windows.
        helpers.write_clip(self.data / "2026-11-25_bench", "POTHOLE-1.csv",
                           helpers.records(CLIP_ROWS, bump_at=1500, seed=5))
        c = wn.build_windows(self.data)
        keep = np.array([cid.startswith("2026-11-24_bench/") for cid in c.clip_ids])
        np.testing.assert_array_equal(c.X[keep], a.X)

    def test_bump_near_the_start(self):
        helpers.write_clip(self.s1, "POTHOLE-1.csv",
                           helpers.records(CLIP_ROWS, bump_at=int(0.3 * RATE)))
        helpers.write_clip(self.s1, "POTHOLE-2.csv",
                           helpers.records(CLIP_ROWS, bump_at=int(0.45 * RATE)))
        reps = by_file(wn.build_windows(self.data))
        self.assertEqual(reps["POTHOLE-1.csv"].windows_cut, 0)          # 0.3 s < 20% of 2 s
        self.assertIn("no window fits", reps["POTHOLE-1.csv"].note)
        self.assertEqual(reps["POTHOLE-2.csv"].windows_cut, 5)          # Only windows that fit
        starts = wn._event_starts(int(0.45 * RATE), CLIP_ROWS, WIDTH, wn.WindowOptions(), "a")
        self.assertTrue(all(s >= 0 for s in starts))

    def test_clip_shorter_than_the_window(self):
        helpers.write_clip(self.s1, "POTHOLE-1.csv", helpers.records(500, bump_at=250))
        rep = by_file(wn.build_windows(self.data))["POTHOLE-1.csv"]
        self.assertEqual(rep.windows_cut, 0)
        self.assertIn("shorter than the window", rep.note)

    def test_older_take(self):
        helpers.write_old_take(self.s1, "SPEED_BUMP-1.csv", bump_at=300)
        rep = by_file(wn.build_windows(self.data))["SPEED_BUMP-1.csv"]
        self.assertEqual(rep.windows_cut, 0)                            # 2.0 s window: skipped
        self.assertIn("older take", rep.note)
        ws = wn.build_windows(self.data, opts=wn.WindowOptions(window_s=1.5))
        self.assertEqual(ws.X.shape, (1, 624, 3))                       # 1.5 s: used (one fits)
        helpers.write_old_take(self.s1, "FALSE_TRIGGER-1.csv", bump_at=300)
        rep = by_file(wn.build_windows(self.data, opts=wn.WindowOptions(window_s=1.5)))
        self.assertIn("relabel", rep["FALSE_TRIGGER-1.csv"].note)

    def test_damaged_windows_skipped_and_listed(self):
        helpers.write_clip(self.s1, "NONE-1.csv", helpers.records(CLIP_ROWS, gap_at=1000))
        helpers.write_clip(self.s1, "NONE-2.csv", helpers.records(CLIP_ROWS, reset_at=2000))
        helpers.write_clip(self.s1, "NONE-3.csv", helpers.records(CLIP_ROWS, ok0=range(50)))
        reps = by_file(wn.build_windows(self.data))
        # Windows start at 0, 416, 832, 1248, 1664 and are 832 rows long.
        self.assertEqual((reps["NONE-1.csv"].windows_cut, reps["NONE-1.csv"].skipped),
                         (3, {"gap": 2}))      # Rows 999 / 1000: windows at 416 and 832
        self.assertEqual((reps["NONE-2.csv"].windows_cut, reps["NONE-2.csv"].skipped),
                         (3, {"reset": 2}))    # Rows 1999 / 2000: windows at 1248 and 1664
        self.assertEqual((reps["NONE-3.csv"].windows_cut, reps["NONE-3.csv"].skipped),
                         (4, {"ok0": 1}))      # Rows 0-49: the window at 0
        self.assertEqual(reps["NONE-1.csv"].as_row()[9], "gap:2")

    def test_bad_files_listed_no_crash(self):
        self.s1.mkdir(parents=True)
        (self.s1 / "POTHOLE-90.csv").write_bytes(b"")
        (self.s1 / "POTHOLE-91.csv").write_text("# format=clip_v1\nnot,numbers\n",
                                                encoding="utf-8")
        (self.s1 / "notes.csv").write_text("hello\n", encoding="utf-8")
        helpers.write_clip(self.s1, "POTHOLE-1.csv", helpers.records(CLIP_ROWS, bump_at=1000))
        reps = by_file(wn.build_windows(self.data))
        self.assertIn("unreadable", reps["POTHOLE-90.csv"].note)
        self.assertIn("unreadable", reps["POTHOLE-91.csv"].note)
        self.assertIn("name is not", reps["notes.csv"].note)
        self.assertEqual(reps["POTHOLE-1.csv"].windows_cut, 5)

    def test_minimum_warning_never_refuses(self):
        helpers.write_clip(self.s1, "POTHOLE-1.csv", helpers.records(CLIP_ROWS, bump_at=1000))
        ws = wn.build_windows(self.data)
        self.assertEqual(len(ws.y), 5)                                  # Still runs
        self.assertEqual(len(ws.warnings), 2)
        self.assertIn("fewer than 30 clips", ws.warnings[0])
        self.assertIn("SPEED_BUMP 0", ws.warnings[0])
        self.assertIn("1 session(s)", ws.warnings[1])

    def test_no_clip_file_changed(self):
        helpers.write_clip(self.s1, "POTHOLE-1.csv",
                           helpers.records(CLIP_ROWS, bump_at=1000, gap_at=1500))
        helpers.write_clip(self.s1, "NONE-1.csv", helpers.records(CLIP_ROWS, reset_at=600))
        helpers.write_old_take(self.s1, "SPEED_BUMP-1.csv", bump_at=300)
        (self.s1 / "POTHOLE-90.csv").write_bytes(b"")
        before = file_hashes(self.data)
        wn.build_windows(self.data)
        wn.build_windows(self.data, opts=wn.WindowOptions(window_s=1.5, decimate=4))
        self.assertEqual(file_hashes(self.data), before)

    def test_decimation(self):
        helpers.write_clip(self.s1, "POTHOLE-1.csv", helpers.records(CLIP_ROWS, bump_at=1000))
        ws = wn.build_windows(self.data, opts=wn.WindowOptions(decimate=4))
        self.assertEqual(ws.X.shape, (5, WIDTH // 4, 3))                # 208 samples
        self.assertEqual(ws.rate_hz, RATE / 4)
        taps = wn.lowpass_taps(4)
        self.assertAlmostEqual(float(taps.sum()), 1.0)                  # Unity gain at 0 Hz
        t = np.arange(4000) / RATE
        for freq, low, high in ((5.0, 0.95, 1.05), (150.0, 0.0, 0.02)):
            out = np.convolve(np.sin(2 * np.pi * freq * t), taps, mode="same")[500:-500]
            gain = np.sqrt(2 * np.mean(out ** 2))
            self.assertTrue(low <= gain <= high, (freq, gain))

    def test_report_csv(self):
        helpers.write_clip(self.s1, "POTHOLE-1.csv", helpers.records(CLIP_ROWS, bump_at=1000))
        helpers.write_clip(self.s1, "NONE-1.csv", helpers.records(CLIP_ROWS))
        ws = wn.build_windows(self.data)
        out = self.data / "windows_report.csv"
        wn.write_report(out, ws.report)
        with open(out, encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        self.assertEqual(tuple(rows[0]), wn.REPORT_COLUMNS)
        self.assertEqual(len(rows), 3)                                  # Every clip listed
        pothole = [r for r in rows if r[1] == "POTHOLE-1.csv"][0]
        self.assertAlmostEqual(float(pothole[6]), 1000 / RATE, delta=TOL_ROWS / RATE)
        self.assertEqual(pothole[7], "5")


if __name__ == "__main__":
    unittest.main()
