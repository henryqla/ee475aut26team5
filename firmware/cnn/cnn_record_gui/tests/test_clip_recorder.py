"""
File:        test_clip_recorder.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.0
Change:      0.1.0 (manual-recording-overhaul-handoff.md, request B): new file.
Description: Unit tests for clip_recorder.py: a press keeps the 1 s before it
             and collects 5 s after it (2496 rows at 416 Hz); every press is
             saved, with gaps, resets, ok=0 rows or a stopped stream counted,
             never refused (O3); presses during a clip or a long recording
             are ignored (O7, B3); a long recording streams its rows to a file.
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
from clip_recorder import (ENDED_CLOSED, ENDED_COMPLETE, ENDED_STOPPED, ENDED_TIMEOUT,
                           ClipRecorder)

# ============================================================================
# Constants
# ============================================================================

EPOCH = 1_790_000_000_000
PRE, POST = 416, 2080          # 1 s and 5 s at 416 Hz

# ============================================================================
# Tests
# ============================================================================


class TestClipRecorder(unittest.TestCase):
    def setUp(self):
        self.rec = ClipRecorder()

    def test_sizes(self):
        self.assertEqual((self.rec._pre_n, self.rec._post_n), (PRE, POST))
        self.rec.set_rate(104)
        self.assertEqual((self.rec._pre_n, self.rec._post_n), (104, 520))

    def test_full_clip(self):
        self.rec.feed(helpers.log_samples(1, 1000))
        self.assertEqual(self.rec.press("POTHOLE", EPOCH, 0.0), "")
        self.assertTrue(self.rec.recording)
        clip = self.rec.feed(helpers.log_samples(1001, POST))
        self.assertIsNotNone(clip)
        self.assertFalse(self.rec.recording)
        self.assertEqual(len(clip.rows), PRE + POST)                  # 2496
        self.assertEqual((clip.press_n, clip.press_row), (1000, PRE - 1))
        self.assertEqual(clip.rows[clip.press_row].n, 1000)          # The last n received
        self.assertEqual((clip.facts.first_n, clip.facts.last_n), (585, 3080))
        self.assertEqual((clip.facts.missing, clip.facts.resets, clip.facts.ok0), (0, 0, 0))
        self.assertEqual(clip.ended_by, ENDED_COMPLETE)
        self.assertEqual(clip.rows_text().count("\n"), PRE + POST)

    def test_samples_after_the_clip_go_to_the_buffer(self):
        self.rec.feed(helpers.log_samples(1, 500))
        self.rec.press("NONE", EPOCH, 0.0)
        clip = self.rec.feed(helpers.log_samples(501, POST + 500))   # One batch, past the end
        self.assertEqual(clip.rows[-1].n, 500 + POST)
        self.rec.press("NONE", EPOCH + 6000, 6.0)
        self.assertEqual(self.rec._cur.press_n, 500 + POST + 500)
        self.assertEqual(len(self.rec._cur.rows), PRE)

    def test_gap_uses_up_time_and_is_counted(self):
        self.rec.feed(helpers.log_samples(1, PRE))
        self.rec.press("POTHOLE", EPOCH, 0.0)
        clip = self.rec.feed(helpers.log_samples(PRE + 1, POST, skip=set(range(900, 920))))
        self.assertIsNotNone(clip)                         # Saved anyway
        self.assertEqual(clip.facts.missing, 20)
        self.assertEqual(clip.facts.last_n, PRE + POST)    # Same device time span
        self.assertEqual(len(clip.rows), PRE + POST - 20)

    def test_reset_and_ok0_counted_never_refused(self):
        self.rec.feed(helpers.log_samples(1, PRE))
        self.rec.press("SPEED_BUMP", EPOCH, 0.0)
        self.assertIsNone(self.rec.feed(helpers.log_samples(PRE + 1, 500)))
        self.rec.stream_restarted()                        # A BOOT mid-clip: it goes on
        clip = self.rec.feed(helpers.log_samples(0, POST - 500, ok0=set(range(0, 50))))
        self.assertIsNotNone(clip)
        self.assertEqual(clip.facts.resets, 1)
        self.assertEqual(clip.facts.ok0, 50)
        self.assertEqual(len(clip.rows), PRE + POST)

    def test_stream_stops_saved_after_grace(self):
        self.rec.feed(helpers.log_samples(1, PRE))
        self.rec.press("POTHOLE", EPOCH, 10.0)
        self.rec.feed(helpers.log_samples(PRE + 1, 100))
        self.assertAlmostEqual(self.rec.remaining_s(12.0), 3.0)
        deadline = 10.0 + cfg.MANUAL_POST_S + cfg.MANUAL_SAVE_GRACE_S
        self.assertIsNone(self.rec.tick(deadline - 0.01))
        clip = self.rec.tick(deadline)
        self.assertEqual(clip.ended_by, ENDED_TIMEOUT)
        self.assertEqual(len(clip.rows), PRE + 100)        # Whatever arrived

    def test_press_with_no_samples_still_saves(self):
        self.assertEqual(self.rec.press("NONE", EPOCH, 0.0), "")
        clip = self.rec.tick(10.0)
        self.assertEqual((len(clip.rows), clip.press_n, clip.press_row), (0, None, None))

    def test_press_during_a_clip_ignored(self):
        self.rec.feed(helpers.log_samples(1, PRE))
        self.rec.press("POTHOLE", EPOCH, 0.0)
        why = self.rec.press("POTHOLE", EPOCH + 500, 0.5)   # Pressed twice within 1 s
        self.assertIn("ignored", why)
        self.assertEqual(self.rec.label, "POTHOLE")
        self.assertIn("ignored", self.rec.start_long(EPOCH, 0.5, Path("unused.partial")))
        self.assertFalse(self.rec.long_active)

    def test_long_recording(self):
        with tempfile.TemporaryDirectory() as tmp:
            body = Path(tmp) / "s1" / ".long-1.partial"
            self.assertEqual(self.rec.start_long(EPOCH, 0.0, body), "")
            self.assertTrue(self.rec.long_active)
            self.assertIn("long", self.rec.press("SPEED_BUMP", EPOCH + 1000, 1.0))   # B3
            self.rec.feed(helpers.log_samples(1, 3000))
            self.rec.feed(helpers.log_samples(3001, 2000, skip={3500}))
            self.assertEqual(self.rec.long_rows(), 4999)
            clip = self.rec.stop_long(EPOCH + 12_000)
            self.assertFalse(self.rec.long_active)
            self.assertEqual((clip.source, clip.label, clip.ended_by),
                             (cfg.SOURCE_LONG, "NONE", ENDED_STOPPED))
            self.assertEqual((clip.facts.rows, clip.facts.missing), (4999, 1))
            self.assertAlmostEqual(clip.post_s, 5000 / 416.0)
            lines = body.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(lines), 4999)
            self.assertEqual(lines[0], helpers.l_line(1, helpers.quiet_z(1))[len("L,"):])  # As received

    def test_finish_all_saves_what_arrived(self):
        self.rec.feed(helpers.log_samples(1, PRE))
        self.rec.press("POTHOLE", EPOCH, 0.0)
        self.rec.feed(helpers.log_samples(PRE + 1, 300))
        clips = self.rec.finish_all(EPOCH + 1000)
        self.assertEqual(len(clips), 1)
        self.assertEqual((clips[0].ended_by, len(clips[0].rows)), (ENDED_CLOSED, PRE + 300))
        self.assertEqual(self.rec.finish_all(EPOCH + 2000), [])

    def test_stream_restart_clears_the_buffer(self):
        self.rec.feed(helpers.log_samples(1, PRE))
        self.rec.stream_restarted()
        self.rec.feed(helpers.log_samples(1, 10))
        self.rec.press("NONE", EPOCH, 0.0)
        self.assertEqual(len(self.rec._cur.rows), 10)       # Nothing from before the reset

    def test_reset_while_idle_clears_the_buffer(self):
        self.rec.feed(helpers.log_samples(500, PRE))
        self.rec.feed(helpers.log_samples(1, 20))           # n went down: the board reset
        self.rec.press("NONE", EPOCH, 0.0)
        self.assertEqual(len(self.rec._cur.rows), 20)
        self.assertEqual(self.rec._cur.facts.resets, 0)


if __name__ == "__main__":
    unittest.main()
