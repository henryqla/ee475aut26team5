"""
File:        test_take_cut.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for take_cut.py, the alignment shared by manual
             Record and cut_windows.py (request 8): the window checks (jump in
             n, time gap, ok=0), the >= threshold comparison, threshold /
             MISS_FRAC / peak alignment, the floor, the edge (the first
             crossing wins, M3), a second event, the session's typical gap,
             and a take's crc16 equal to the SNE CRC of the same rows.
"""

# ============================================================================
# Imports
# ============================================================================

import tempfile
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
import cnn_config as cfg
import take_cut as tc
from snapshot_protocol import Snapshot, SnapshotAssembler
from take_store import TakeStore, header_text

# ============================================================================
# Functions
# ============================================================================

THR = tc.thresholds_from_boot(tc.boot_settings(helpers.LOG_BOOT))


def rows_with(events, count=2496, first_n=1000):
    """
    @param events  {row index: (z, roll, pitch)}.
    @return count LogSample rows starting at n = first_n.
    """
    return helpers.log_samples(first_n, count, {first_n + k: v for k, v in events.items()})


def bump_at(index, amp):
    """
    @return A bump starting at a row index (for rows_with).
    """
    return {k - 1000: v for k, v in helpers.bump(1000 + index, amp).items()}

# ============================================================================
# Tests
# ============================================================================


class TestBootAndWindows(unittest.TestCase):
    def test_thresholds_from_boot(self):
        self.assertEqual((THR.z, THR.roll, THR.pitch), (1.0, 60.0, 60.0))
        self.assertEqual(tc.boot_problem(tc.boot_settings(helpers.LOG_BOOT)), "")
        self.assertIn("no BOOT", tc.boot_problem(tc.boot_settings("missing (lost)")))
        self.assertIn("thr_z", tc.boot_problem(tc.boot_settings("BOOT,fw=x,odr=416")))

    def test_reaches_is_greater_or_equal(self):
        rows = rows_with({10: (1.0, 0.0, 0.0), 20: (0.9999, 0.0, 0.0), 30: (0.0, -60.0, 0.0)})
        self.assertTrue(THR.reaches(rows[10], 1.0))
        self.assertFalse(THR.reaches(rows[20], 1.0))
        self.assertTrue(THR.reaches(rows[30], 1.0))
        self.assertTrue(THR.reaches(rows[20], 0.5))

    def test_window_problems(self):
        good = helpers.log_samples(1, 700)
        self.assertIsNone(tc.window_problem(good, 0, 700))
        self.assertIn("too few rows before", tc.window_problem(good, -3, 600))
        self.assertIn("too few rows after", tc.window_problem(good, 100, 701))
        jump = good[:300] + good[301:]
        self.assertIn("jump in n (300 to 302)", tc.window_problem(jump, 0, 699))
        late = helpers.log_samples(1, 700, t_jump_at=400)
        self.assertIn("gap in t_us", tc.window_problem(late, 0, 700))
        settle = helpers.log_samples(1, 10, ok=0) + helpers.log_samples(11, 690)
        self.assertIn("ok=0 at n 1 ", tc.window_problem(settle, 0, 700))
        self.assertIn("ok=0 at n 10 ", tc.window_problem(settle, 9, 700))
        self.assertIsNone(tc.window_problem(settle, 10, 700))


class TestAlign(unittest.TestCase):
    def test_threshold(self):
        rows = rows_with(bump_at(1500, 1.3))
        al = tc.align(rows, THR, tc.MANUAL_LEVELS, 0)
        self.assertTrue(al.ok, al.problem)
        self.assertEqual((al.kind, al.index), (tc.ALIGN_THRESHOLD, 1500))
        self.assertEqual(al.peak, 1500)
        self.assertAlmostEqual(al.peak_frac, 1.3)
        self.assertIsNone(al.second)

    def test_miss_frac(self):
        al = tc.align(rows_with(bump_at(1500, 0.7)), THR, tc.MANUAL_LEVELS, 0)
        self.assertEqual((al.kind, al.index, al.level), (tc.ALIGN_MISS_FRAC, 1500, cfg.MISS_FRAC))

    def test_weak_event_peak_aligned(self):
        al = tc.align(rows_with(bump_at(1500, 0.3)), THR, tc.MANUAL_LEVELS, 40)
        self.assertEqual((al.kind, al.peak, al.gap, al.index), (tc.ALIGN_PEAK, 1500, 40, 1460))
        self.assertTrue(al.ok)

    def test_below_floor_nothing(self):
        al = tc.align(rows_with(bump_at(1500, 0.15)), THR, tc.MANUAL_LEVELS, 0)
        self.assertFalse(al.ok)
        self.assertEqual(al.problem_kind, tc.NOTHING)

    def test_first_crossing_wins_even_at_the_edge(self):
        # MR-3: a tap in the first 0.2 s, then a later one: no take.
        events = dict(bump_at(50, 1.5))
        events.update(bump_at(1500, 1.5))
        al = tc.align(rows_with(events), THR, tc.MANUAL_LEVELS, 0)
        self.assertEqual((al.index, al.problem_kind), (50, tc.EDGE))

    def test_end_edge(self):
        al = tc.align(rows_with(bump_at(2400, 1.5)), THR, tc.MANUAL_LEVELS, 0)
        self.assertEqual(al.problem_kind, tc.EDGE)

    def test_second_event_reported(self):
        events = dict(bump_at(800, 1.5))
        events.update(bump_at(1800, 1.5))
        al = tc.align(rows_with(events), THR, tc.MANUAL_LEVELS, 0)
        self.assertEqual((al.index, al.second), (800, 1800))
        self.assertIn("second event 2.4 s later", tc.describe(al, 416))

    def test_window_with_a_gap_refused(self):
        rows = rows_with(bump_at(1500, 1.5))
        del rows[1700]
        al = tc.align(rows, THR, tc.MANUAL_LEVELS, 0)
        self.assertEqual(al.problem_kind, tc.WINDOW)
        self.assertIn("jump in n", al.problem)


class TestTakeText(unittest.TestCase):
    def test_crc_equals_the_snapshots(self):
        # The SNR lines the record firmware would send for the same rows.
        rows = rows_with(bump_at(1500, 1.3))
        lo, hi = 1500 - cfg.TAKE_N_PRE, 1500 + cfg.TAKE_N_POST
        lines = ["SNB,3,%d,416,104,520,624,az_hp/gx/gy,0.1.3-bench" % rows[1500].n]
        lines += ["SNR,%d,%s,%s,%s" % ((i,) + rows[k].model_text())
                  for i, k in enumerate(range(lo, hi))]
        import binascii
        crc = 0xFFFF
        for line in lines[1:]:
            crc = binascii.crc_hqx(line.encode("ascii"), crc)
        lines.append("SNE,3,624,%04X" % crc)
        asm = SnapshotAssembler(3.0, 20000)
        snap = [x for line in lines for x in asm.feed(line, 0.0)][0]
        self.assertIsInstance(snap, Snapshot)
        self.assertEqual(tc.snapshot_crc(rows, lo, hi), snap.crc16)
        self.assertEqual(tc.take_rows_text(rows, lo, hi).splitlines(),
                         [",".join(r) for r in snap.rows])

    def test_typical_peak_gap(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = TakeStore(Path(tmp.name))
        for k, gap in enumerate((30, 40, 40, 50, 41, 39, 40, 60, 20, 40)):
            text = header_text([("label", "POTHOLE"), ("trig_idx", 1000 * k),
                                ("rule_n_peak", 1000 * k + gap)]) + "0,0,0\n"
            store.write_take("s1", "POTHOLE", text)
            if k == 8:   # Nine takes: below PEAK_GAP_MIN_TAKES
                self.assertEqual(tc.typical_peak_gap(store.session_dir("s1")), (0, 9))
        self.assertEqual(tc.typical_peak_gap(store.session_dir("s1")), (40, 10))
        # Takes cut at an EVT count too; manual ones do not.
        store.write_take("s1", "POTHOLE", header_text([("source", "evt"), ("n_trig", 5),
                                                       ("n_peak", 1005)]) + "0,0,0\n")
        store.write_take("s1", "POTHOLE", header_text([("source", "manual"), ("trig_idx", 5),
                                                       ("n_peak", 2005)]) + "0,0,0\n")
        self.assertEqual(tc.typical_peak_gap(store.session_dir("s1")), (40, 11))


if __name__ == "__main__":
    unittest.main()
