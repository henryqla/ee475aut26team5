"""
File:        test_manual_record.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for manual_record.py (request 5, changed by request
             8): the 3 s buffer and 3 s after the press; a press before and
             after a bump both give one 624-row take with the crossing at row
             104 (MR-2); weak, nothing, edge and two-event recordings (MR-3);
             gaps, resets, ok=0 and a lost port save nothing; presses during a
             recording are ignored; the raw recording and its take share a
             src_key, and re-cutting the raw file gives the same bytes (MR-4).
"""

# ============================================================================
# Imports
# ============================================================================

import tempfile
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
import cnn_config as cfg
from manual_record import NO_TAKE, NOT_SAVED, SAVED, WEAK, ManualRecorder, save_capture
from take_cut import cut_manual_file, typical_peak_gap
from take_store import load_take, read_header

# ============================================================================
# Constants
# ============================================================================

SESSION = "2026-11-22_bench"
PRE = int(cfg.MANUAL_PRE_S * 416)      # 1248
POST = int(cfg.MANUAL_POST_S * 416)    # 1248

# ============================================================================
# Tests
# ============================================================================


class TestManualRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from take_store import TakeStore
        self.store = TakeStore(Path(self.tmp.name))
        self.rec = ManualRecorder()

    def record(self, events=None, press_at=3000, end=6000, label="POTHOLE", rows=None):
        """
        Feeds samples n = 1..end in batches of 21 (about 50 ms), pressing
        Record once sample press_at has been fed.
        @return The finished Capture.
        """
        rows = rows if rows is not None else helpers.log_samples(1, end, events)
        cap = None
        for k in range(0, len(rows), 21):
            batch = rows[k:k + 21]
            done = self.rec.feed(batch)
            if done is not None:
                cap = done
            if batch[0].n <= press_at <= batch[-1].n:
                self.assertEqual(self.rec.press(label, 1795000000000 + k, helpers.LOG_BOOT), "")
        return cap

    def test_buffer_sizes(self):
        cap = self.record()
        self.assertEqual(cap.problem, "")
        self.assertEqual(len(cap.rows), PRE + POST)        # About 2496 rows (DRV-3a)
        self.assertEqual(cap.rows_before, PRE)
        self.assertEqual(cap.rows[cap.rows_before - 1].n, cap.press_n)

    def check_take(self, out, crossing_n):
        """The take is in the session folder, 624 rows, crossing at row 104."""
        self.assertEqual(out.state, SAVED, out.message)
        self.assertEqual(out.take_path.parent.name, SESSION)
        header, values = load_take(out.take_path)
        self.assertEqual(len(values), cfg.TAKE_N_ROWS)
        self.assertEqual(header["align_n"], str(crossing_n))
        self.assertEqual(header["trig_idx"], str(crossing_n))
        self.assertGreaterEqual(abs(values[cfg.TAKE_N_PRE][0]), 1.0)   # Row 105 (index 104)
        self.assertLess(abs(values[cfg.TAKE_N_PRE - 1][0]), 1.0)
        self.assertEqual(header["source"], "manual")
        # The raw recording is in manual/ with the same src_key.
        raw = read_header(out.raw_path)
        self.assertEqual(out.raw_path.parent.name, cfg.MANUAL_DIR_NAME)
        self.assertEqual(raw["src_key"], header["src_key"])
        return header

    def test_press_then_bump(self):
        cap = self.record(helpers.bump(3400, 1.4), press_at=3000)
        out = save_capture(self.store, SESSION, cap, "abc123")
        self.check_take(out, 3400)

    def test_bump_then_press(self):
        cap = self.record(helpers.bump(2300, 1.4), press_at=3000)
        out = save_capture(self.store, SESSION, cap, "abc123")
        self.check_take(out, 2300)

    def test_recut_gives_the_same_bytes(self):
        # MR-4: cut_windows.py's cut of the raw file equals the GUI's take.
        cap = self.record(helpers.bump(3400, 1.4))
        out = save_capture(self.store, SESSION, cap, "abc123")
        session_dir = self.store.session_dir(SESSION)
        again = cut_manual_file(out.raw_path, session_dir, typical_peak_gap(session_dir)[0])
        self.assertTrue(again.ok)
        self.assertEqual(again.text, out.take_path.read_text(encoding="utf-8"))

    def test_half_threshold(self):
        out = save_capture(self.store, SESSION, self.record(helpers.bump(3400, 0.7)), "c")
        self.assertEqual(out.state, SAVED)
        self.assertEqual(read_header(out.take_path)["align"], "miss_frac")

    def test_weak_goes_to_weak_folder(self):
        out = save_capture(self.store, SESSION, self.record(helpers.bump(3400, 0.3)), "c")
        self.assertEqual(out.state, WEAK, out.message)
        self.assertEqual(out.take_path.parent.name, cfg.WEAK_DIR_NAME)
        self.assertEqual(read_header(out.take_path)["align"], "peak")

    def test_nothing_found(self):
        out = save_capture(self.store, SESSION, self.record(), "c")
        self.assertEqual(out.state, NO_TAKE)
        self.assertTrue(out.message.startswith("Nothing found near the press"))
        self.assertTrue(out.raw_path.exists())           # M1: the raw recording is kept
        self.assertEqual(self.store.list_takes(SESSION), [])

    def test_bump_at_the_start_is_too_close_to_the_edge(self):
        # The press comes after the batch ending at n 3003, so the buffer
        # starts at n 1756; a tap 37 samples (under 0.1 s) later.
        events = dict(helpers.bump(1793, 1.5))
        events.update(helpers.bump(3400, 1.5))
        out = save_capture(self.store, SESSION, self.record(events), "c")
        self.assertEqual(out.state, NO_TAKE)
        self.assertEqual(out.message, "Event too close to the edge of the recording")

    def test_two_bumps_first_wins(self):
        events = dict(helpers.bump(2300, 1.5))
        events.update(helpers.bump(3600, 1.5))
        out = save_capture(self.store, SESSION, self.record(events), "c")
        header = self.check_take(out, 2300)
        self.assertIn("second event", out.message)
        self.assertEqual(header["label"], "POTHOLE")

    def test_gap_reset_ok0_not_saved(self):
        rows = helpers.log_samples(1, 6000, helpers.bump(3400, 1.4))
        del rows[3500]                                    # A jump in n
        cap = self.record(rows=rows)
        self.assertIn("jump in n", cap.problem)
        out = save_capture(self.store, SESSION, cap, "c")
        self.assertEqual(out.state, NOT_SAVED)
        self.assertFalse((self.store.session_dir(SESSION)).exists())   # Nothing written

        rec_rows = helpers.log_samples(1, 3500) + helpers.log_samples(1, 2000)  # Reset
        self.rec = ManualRecorder()
        self.assertIn("reset", self.record(rows=rec_rows).problem)

        ok0 = helpers.log_samples(1, 3200) + helpers.log_samples(3201, 10, ok=0) + \
            helpers.log_samples(3211, 2800)
        self.rec = ManualRecorder()
        self.assertIn("ok=0", self.record(rows=ok0).problem)

    def test_interrupt_and_ignored_press(self):
        rows = helpers.log_samples(1, 4000)
        self.rec.feed(rows[:3000])
        self.assertEqual(self.rec.press("SPEED_BUMP", 1, helpers.LOG_BOOT), "")
        self.assertIn("already", self.rec.press("POTHOLE", 2, helpers.LOG_BOOT))
        self.rec.set_label("FALSE_TRIGGER")      # A key during the countdown
        self.assertIsNone(self.rec.feed(rows[3000:3500]))
        self.assertGreater(self.rec.remaining_s(), 0.0)
        cap = self.rec.interrupt("USB lost during the recording")
        self.assertEqual((cap.label, cap.problem),
                         ("FALSE_TRIGGER", "USB lost during the recording"))
        self.assertFalse(self.rec.recording)
        self.assertIn("no L samples", self.rec.press("POTHOLE", 3, helpers.LOG_BOOT))

    def test_five_presses_five_files(self):
        # DRV-3c: five recordings in a row, numbers never repeat.
        rows = helpers.log_samples(1, 20000, helpers.bump(3400, 1.4))
        names = []
        for k in range(0, len(rows), 21):
            cap = self.rec.feed(rows[k:k + 21])
            if cap is not None:
                names.append(save_capture(self.store, SESSION, cap, "c").raw_path.name)
            if not self.rec.recording and len(names) < 5 and rows[k].n >= 1300:
                self.rec.press("POTHOLE", 1795000000000 + k, helpers.LOG_BOOT)
        self.assertEqual(names, ["POTHOLE-%d.csv" % i for i in range(1, 6)])

    def test_no_boot_keeps_raw_without_take(self):
        rows = helpers.log_samples(1, 6000, helpers.bump(3400, 1.4))
        self.rec.feed(rows[:3000])
        self.rec.press("POTHOLE", 5, None)
        cap = self.rec.feed(rows[3000:])
        out = save_capture(self.store, SESSION, cap, "c")
        self.assertEqual(out.state, NO_TAKE)
        self.assertIn("no BOOT line", out.message)


if __name__ == "__main__":
    unittest.main()
