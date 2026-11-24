"""
File:        test_take_store.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B): the
             snapshot take, missing-EVT, subfolder and src_key tests are
             removed with save_take(), the subfolders and src_keys(). The
             numbering, relabel, bad-input, file-name and temp-file tests are
             kept, with their files written by write_take() instead. New: an
             older take (FALSE_TRIGGER) can be listed and relabeled to NONE,
             write_take_streamed() copies a rows file, and the labels are the
             three classes.
Description: Unit tests for take_store.py: names, numbering that never reuses
             a number, relabel (header updated, file time kept), delete into
             _deleted, atomic writes, and the long-recording writer.
             Run from cnn_record_gui/: python -m unittest discover -s tests
"""

# ============================================================================
# Imports
# ============================================================================

import os
import tempfile
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
from take_store import TakeStore, load_take, parse_take_name, read_header

# ============================================================================
# Constants
# ============================================================================

S = "2026-11-12_bench"
TEXT = "# format=clip_v1\n# label=%s\n1,2,3\n4,5,6\n"

# ============================================================================
# Tests
# ============================================================================


class TestTakeStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TakeStore(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, label="POTHOLE", session=S):
        return self.store.write_take(session, label, TEXT % label)

    def test_name_and_text(self):
        path = self.save()
        self.assertEqual(path.name, "POTHOLE-1.csv")
        self.assertEqual(path.parent.name, S)
        with open(path, encoding="utf-8", newline="") as fh:
            self.assertEqual(fh.read(), TEXT % "POTHOLE")     # Written exactly, LF endings

    def test_numbering_and_no_reuse_after_delete(self):
        a = self.save()
        b = self.save()
        self.assertEqual((a.name, b.name), ("POTHOLE-1.csv", "POTHOLE-2.csv"))
        self.store.delete(b)
        c = self.save()
        self.assertEqual(c.name, "POTHOLE-3.csv")
        self.assertTrue((a.parent / "_deleted" / "POTHOLE-2.csv").exists())
        self.assertEqual([p.name for p in self.store.list_takes(S)],
                         ["POTHOLE-1.csv", "POTHOLE-3.csv"])

    def test_relabel(self):
        a = self.save(label="NONE")
        os.utime(a, (1000.0, 1000.0))
        new = self.store.relabel(a, "SPEED_BUMP")
        self.assertEqual(new.name, "SPEED_BUMP-1.csv")
        self.assertFalse(a.exists())
        header, values = load_take(new)
        self.assertEqual(header["label"], "SPEED_BUMP")
        self.assertTrue(header["relabeled"].startswith("NONE>SPEED_BUMP at "))
        self.assertEqual(values, [(1.0, 2.0, 3.0), (4.0, 5.0, 6.0)])
        self.assertEqual(new.stat().st_mtime, 1000.0)  # Keeps recording order

    def test_relabel_same_label_is_noop(self):
        a = self.save()
        self.assertEqual(self.store.relabel(a, "POTHOLE"), a)

    def test_older_take_listed_and_relabeled(self):
        folder = Path(self.tmp.name) / S
        folder.mkdir()
        old = folder / "FALSE_TRIGGER-4.csv"
        old.write_text(helpers.old_take_text("FALSE_TRIGGER"), encoding="utf-8")
        self.assertEqual(self.store.list_takes(S), [old])       # Listed as it is
        new = self.store.relabel(old, "NONE")                    # Only by the owner
        self.assertEqual(new.name, "NONE-1.csv")
        self.assertEqual(read_header(new)["label"], "NONE")
        self.assertEqual(len(load_take(new)[1]), 624)

    def test_bad_inputs(self):
        for label in ("SPEED-BUMP", "FALSE_TRIGGER", "UNLABELED"):   # Not one of the 3 classes
            with self.assertRaises(ValueError, msg=label):
                self.save(label=label)
        for bad in ("", "../x", "a/b", "has space", "_x", "CON", "x" * 70):
            with self.assertRaises(ValueError, msg=bad):
                self.save(session=bad)
        with self.assertRaises(ValueError):
            self.store.relabel(self.save(), "FALSE_TRIGGER")
        self.assertEqual(os.listdir(Path(self.tmp.name) / S), ["POTHOLE-1.csv"])

    def test_parse_take_name(self):
        self.assertEqual(parse_take_name(Path("SPEED_BUMP-12.csv")), ("SPEED_BUMP", 12))
        self.assertIsNone(parse_take_name(Path("POTHOLE-3.dup1.csv")))
        self.assertIsNone(parse_take_name(Path(".POTHOLE-1.csv.tmp")))
        self.assertIsNone(parse_take_name(Path(".long-1790000000000.partial")))

    def test_no_temp_files_left(self):
        path = self.save()
        self.assertEqual(sorted(os.listdir(path.parent)), ["POTHOLE-1.csv"])

    def test_write_take_streamed(self):
        body = Path(self.tmp.name) / "rows.partial"
        body.write_text("".join("%d,0\n" % i for i in range(5000)), encoding="utf-8")
        path = self.store.write_take_streamed(S, "NONE", "# format=clip_v1\n# label=NONE\n", body)
        self.assertEqual(path.name, "NONE-1.csv")
        header, values = load_take(path)
        self.assertEqual(header, {"format": "clip_v1", "label": "NONE"})
        self.assertEqual(len(values), 5000)
        self.assertEqual(values[-1], (4999.0, 0.0))
        self.assertTrue(body.exists())                   # The caller removes it
        self.assertEqual(sorted(os.listdir(path.parent)), ["NONE-1.csv"])

    def test_read_header_stops_at_data(self):
        path = self.store.write_take(S, "POTHOLE", "# a=1\n# label=POTHOLE\n1,2,3\n# x=y\n")
        self.assertEqual(read_header(path), {"a": "1", "label": "POTHOLE"})


if __name__ == "__main__":
    unittest.main()
