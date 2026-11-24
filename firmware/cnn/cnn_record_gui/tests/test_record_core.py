"""
File:        test_record_core.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B): the arm,
             EVT matching and quick label tests are removed with those
             features. The BOOT / CFG precedence test is kept, rewritten
             against the new controller. New: saving a clip (header and rows),
             a long recording saved from its rows file, a write error reported
             and nothing lost, Backspace's delete_last(), and the session's
             clip counts.
Description: Unit tests for record_core.py.
             Run from cnn_record_gui/: python -m unittest discover -s tests
"""

# ============================================================================
# Imports
# ============================================================================

import tempfile
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
import cnn_config as cfg
from clip_recorder import ClipRecorder
from record_core import RecordController
from snapshot_protocol import BootLine, LineParser
from take_store import TakeStore, load_take, read_header

# ============================================================================
# Functions
# ============================================================================


def cfg_boot(fields):
    """
    @param fields  A CFG line's text after "CFG,".
    @return The BOOT line that CFG repeats (ConfigLine.as_boot()).
    """
    return LineParser().feed("CFG," + fields).as_boot()


def manual_clip(label="POTHOLE"):
    """
    @return A full manual clip (416 + 2080 rows) from a ClipRecorder.
    """
    rec = ClipRecorder()
    rec.feed(helpers.log_samples(1, 416))
    rec.press(label, 1_790_000_000_000, 0.0)
    clip = rec.feed(helpers.log_samples(417, 2080))
    assert clip is not None
    return clip

# ============================================================================
# Tests
# ============================================================================


class TestRecordController(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TakeStore(Path(self.tmp.name))
        self.saved = []
        self.errors = []
        self.c = RecordController(self.store, "abc123",
                                  on_saved=lambda p, clip: self.saved.append(p),
                                  on_error=self.errors.append)
        self.c.session = "s1"

    def tearDown(self):
        self.tmp.cleanup()

    def test_cfg_used_only_without_boot(self):
        boot = BootLine({"fw": "x"}, "BOOT,fw=x")
        self.c.on_boot(boot)
        self.assertFalse(self.c.on_settings(cfg_boot("fw=y")))
        self.assertIs(self.c.boot, boot)
        self.c.on_reconnect()                               # The new BOOT may be lost
        self.assertTrue(self.c.on_settings(cfg_boot("fw=y")))
        self.assertEqual(self.c.boot.raw, "BOOT,fw=y")     # Kept as BOOT
        self.c.on_boot(boot)                                # A real reboot wins
        self.assertIs(self.c.boot, boot)
        self.c.on_new_connection()
        self.assertIsNone(self.c.boot)

    def test_save_clip_header_and_rows(self):
        self.c.on_boot(LineParser().feed(helpers.BOOT))
        path = self.c.save_clip(manual_clip("SPEED_BUMP"))
        self.assertEqual(self.saved, [path])
        self.assertEqual(path.name, "SPEED_BUMP-1.csv")
        header, values = load_take(path)
        expect = {"format": "clip_v1", "source": "manual", "label": "SPEED_BUMP",
                  "session": "s1", "press_epoch_ms": "1790000000000", "press_n": "416",
                  "press_row": "415", "first_n": "1", "last_n": "2496", "pre_s": "1.000",
                  "post_s": "5.000", "rate_hz": "416", "rows": "2496", "missing_samples": "0",
                  "resets": "0", "ok0_rows": "0", "boot": helpers.BOOT, "gui_commit": "abc123",
                  "columns": "n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok"}
        for k, v in expect.items():
            self.assertEqual(header.get(k), v, k)
        self.assertIn("press_pc", header)
        self.assertEqual(len(values), 2496)
        self.assertEqual(self.c.last_saved, path)
        self.assertEqual(self.c.counters.saved, 1)

    def test_boot_missing_is_written(self):
        header = read_header(self.c.save_clip(manual_clip()))
        self.assertTrue(header["boot"].startswith("missing"))

    def test_long_recording_saved_from_its_rows_file(self):
        rec = ClipRecorder()
        body = Path(self.tmp.name) / "s1" / ".long-1.partial"
        self.assertEqual(rec.start_long(1_790_000_000_000, 0.0, body), "")
        rec.feed(helpers.log_samples(1, 1000))
        clip = rec.stop_long(1_790_000_002_404)
        path = self.c.save_clip(clip)
        self.assertEqual(path.name, "NONE-1.csv")
        self.assertFalse(body.exists())                    # Removed once saved
        header, values = load_take(path)
        self.assertEqual((header["source"], header["rows"], header["post_s"]),
                         ("long", "1000", "%.3f" % (1000 / 416.0)))
        self.assertEqual(header["stop_epoch_ms"], "1790000002404")
        self.assertEqual(len(values), 1000)

    def test_save_error_reported_not_raised(self):
        self.c.session = "bad name"                        # Not a valid folder name
        self.assertIsNone(self.c.save_clip(manual_clip()))
        self.assertEqual(self.c.counters.save_errors, 1)
        self.assertEqual(len(self.errors), 1)
        self.assertIsNone(self.c.last_saved)

    def test_delete_last_once(self):
        a = self.c.save_clip(manual_clip())
        old, dest = self.c.delete_last()
        self.assertEqual(old, a)
        self.assertEqual(dest.parent.name, cfg.DELETED_DIR_NAME)
        self.assertFalse(a.exists())
        self.assertIsNone(self.c.delete_last())            # Once only
        self.assertEqual(self.c.counters.deleted, 1)

    def test_take_moved_follows_relabel(self):
        a = self.c.save_clip(manual_clip())
        b = self.store.relabel(a, "NONE")
        self.c.take_moved(a, b)
        self.assertEqual(self.c.last_saved, b)
        self.c.take_moved(b, None)
        self.assertIsNone(self.c.last_saved)

    def test_session_counts(self):
        for label in ("POTHOLE", "POTHOLE", "NONE"):
            self.c.save_clip(manual_clip(label))
        rec = ClipRecorder()
        rec.start_long(1, 0.0, Path(self.tmp.name) / "s1" / ".long-2.partial")
        rec.feed(helpers.log_samples(1, 832))
        self.c.save_clip(rec.stop_long(2))
        (Path(self.tmp.name) / "s1" / "FALSE_TRIGGER-1.csv").write_text(
            helpers.old_take_text("FALSE_TRIGGER"), encoding="utf-8")
        counts = self.c.session_counts()
        self.assertEqual(counts.per_label, {"POTHOLE": 2, "NONE": 1})
        self.assertEqual(counts.long_count, 1)
        self.assertAlmostEqual(counts.long_s, 2.0, places=2)
        self.assertEqual(counts.older, 1)
        text = counts.text("s1")
        for part in ("POTHOLE 2", "SPEED_BUMP 0", "NONE 1", "long NONE 1", "older takes 1"):
            self.assertIn(part, text)


if __name__ == "__main__":
    unittest.main()
