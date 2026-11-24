"""
File:        test_clip_file.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.0
Change:      0.1.0 (manual-recording-overhaul-handoff.md, request B): new file.
Description: Unit tests for clip_file.py: the facts (missing samples, resets,
             ok=0 rows), the clip header's fields and order, rows written
             exactly as received, and load_clip() on a clip, an older
             snapshot-era take, an empty clip and files that are neither.
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
from clip_file import (KIND_CLIP, KIND_TAKE, ClipFacts, clip_header, info_lines, load_clip)
from take_store import TakeStore

# ============================================================================
# Constants
# ============================================================================

S = "2026-11-23_bench"

# The handoff's header fields, in its order (with the added ones between).
HANDOFF_ORDER = ("format", "source", "label", "press_epoch_ms", "press_n", "first_n", "last_n",
                 "pre_s", "post_s", "rows", "missing_samples", "resets", "ok0_rows", "boot",
                 "gui_commit")

# ============================================================================
# Functions
# ============================================================================


def facts_of(samples):
    """
    @param samples  LogSample list.
    @return Their ClipFacts.
    """
    f = ClipFacts()
    for s in samples:
        f.add(s.n, s.ok)
    return f


def header_keys(text):
    """
    @param text  A "# key=value" header.
    @return Its keys, in order.
    """
    return [line[2:].partition("=")[0] for line in text.splitlines()]

# ============================================================================
# Tests
# ============================================================================


class TestClipFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TakeStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def write_clip(self, samples, label="POTHOLE", press_row=415):
        """
        @return The path of a manual clip of these samples.
        """
        f = facts_of(samples)
        head = clip_header(cfg.SOURCE_MANUAL, label, S, 1_790_000_000_000,
                           samples[press_row].n if samples else None,
                           press_row if samples else None, 1.0, 5.0, 416, f, helpers.BOOT, "abc")
        rows = "".join(s.row_text() + "\n" for s in samples)
        return self.store.write_take(S, label, head + rows)

    def test_facts(self):
        f = facts_of(helpers.log_samples(1, 10, skip={4, 5}, ok0={9}) +
                     helpers.log_samples(1, 3))            # Then the board resets
        self.assertEqual((f.rows, f.missing, f.resets, f.ok0), (11, 2, 1, 1))
        self.assertEqual((f.first_n, f.last_n, f.span), (1, 3, 13))

    def test_header_fields_in_order(self):
        head = clip_header(cfg.SOURCE_MANUAL, "NONE", S, 1_790_000_000_000, 416, 415, 1.0, 5.0,
                           416, facts_of(helpers.log_samples(1, 2496)), None, "abc")
        keys = header_keys(head)
        self.assertEqual([k for k in keys if k in HANDOFF_ORDER], list(HANDOFF_ORDER))
        self.assertEqual(keys[-1], "columns")
        self.assertIn("# boot=missing", head)
        self.assertNotIn("stop_epoch_ms", keys)
        long_head = clip_header(cfg.SOURCE_LONG, "NONE", S, 1, None, None, 0.0, 30.0, 416,
                                ClipFacts(), helpers.BOOT, "abc", stop_epoch_ms=30001)
        self.assertIn("# stop_epoch_ms=30001", long_head)
        self.assertIn("# press_n=none", long_head)
        self.assertIn("# first_n=none", long_head)

    def test_rows_exactly_as_received(self):
        samples = helpers.log_samples(1, 2496)
        path = self.write_clip(samples)
        with open(path, encoding="utf-8") as fh:
            data = [line.rstrip("\n") for line in fh if not line.startswith("#")]
        self.assertEqual(len(data), 2496)
        self.assertEqual(data[0], helpers.l_line(1, helpers.quiet_z(1))[len("L,"):])

    def test_load_clip(self):
        samples = helpers.log_samples(1, 2496, skip={100}, ok0={200, 201},
                                      events=helpers.bump(1000, amp=2.0))
        clip = load_clip(self.write_clip(samples))
        self.assertEqual((clip.kind, clip.label, clip.source), (KIND_CLIP, "POTHOLE", "manual"))
        self.assertEqual((clip.rows, clip.rate_hz, clip.press_row), (2495, 416.0, 415))
        self.assertEqual(clip.model.shape, (2495, 3))
        self.assertEqual(int(clip.n[99]), 101)                       # The jump in n
        self.assertEqual(int(clip.ok.sum()), 2493)
        self.assertAlmostEqual(float(clip.model[999 - 1, 0]), 2.0)   # n 1000 is row 998
        self.assertEqual(clip.header["missing_samples"], "1")

    def test_load_older_take(self):
        folder = Path(self.tmp.name) / S
        folder.mkdir()
        path = folder / "SPEED_BUMP-2.csv"
        path.write_text(helpers.old_take_text("SPEED_BUMP"), encoding="utf-8")
        clip = load_clip(path)
        self.assertEqual((clip.kind, clip.rows, clip.press_row, clip.source),
                         (KIND_TAKE, 624, 104, "take"))
        self.assertIsNone(clip.n)
        self.assertTrue(any(name == "Rule class" for name, _ in info_lines(clip)))

    def test_empty_clip_loads(self):
        clip = load_clip(self.write_clip([]))
        self.assertEqual((clip.rows, clip.press_row), (0, None))
        self.assertEqual(clip.model.shape, (0, 3))

    def test_bad_files(self):
        folder = Path(self.tmp.name) / S
        folder.mkdir()
        no_header = folder / "POTHOLE-9.csv"
        no_header.write_text("1,2,3\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_clip(no_header)
        garbage = folder / "POTHOLE-10.csv"
        garbage.write_text("# format=clip_v1\nhello,world\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_clip(garbage)

    def test_info_lines_are_facts(self):
        clip = load_clip(self.write_clip(helpers.log_samples(1, 2496, skip={50})))
        info = dict(info_lines(clip))
        self.assertEqual(info["Missing samples"], "1")
        self.assertEqual(info["Rows"], "2495 (full clip: 2496)")
        self.assertEqual(info["Firmware"], "0.1.4-bench")


if __name__ == "__main__":
    unittest.main()
