"""
File:        test_take_store.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Description: Unit tests for take_store.py: file name and header of a saved
             take, rows exactly as sent, numbering that never reuses a number,
             relabel (name, header, file time), soft delete, and refusal of bad
             labels and session names. Uses a temporary folder.
"""

# ============================================================================
# Imports
# ============================================================================

import os
import tempfile
import time
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
from snapshot_protocol import SnapshotAssembler
from take_store import TakeStore, load_take, parse_take_name, read_header

# ============================================================================
# Functions
# ============================================================================


def make_snapshot_and_evt(seq=7):
    """
    @return (Snapshot, EventLine) parsed from firmware-format lines.
    """
    asm = SnapshotAssembler(3.0, 20000)
    lines, _ = helpers.firmware_snapshot_lines(seq=seq)
    items = []
    for line in lines + [helpers.evt_line(seq=seq)]:
        items.extend(asm.feed(line, 0.0))
    return items[0], items[1]

# ============================================================================
# Tests
# ============================================================================


class TestTakeStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TakeStore(Path(self.tmp.name))
        self.snap, self.evt = make_snapshot_and_evt()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, label="POTHOLE", evt=True, session="2026-11-12_bench"):
        return self.store.save_take(session=session, label=label, snap=self.snap,
                                    evt=self.evt if evt else None,
                                    boot_raw="BOOT,fw=0.1.2-bench,rec=1",
                                    gui_commit="abc123", received_wall=time.time(),
                                    evt_missing_reason="no EVT within 5.0 s")

    def test_save_name_header_rows(self):
        path = self.save()
        self.assertEqual(path.name, "POTHOLE-1.csv")
        self.assertEqual(path.parent.name, "2026-11-12_bench")
        header, values = load_take(path)
        expect = {"take_format": "1", "label": "POTHOLE", "session": "2026-11-12_bench",
                  "gui_commit": "abc123", "fw": "0.1.2-bench", "seq": "7",
                  "trig_idx": "12345", "rate_hz": "416", "n_pre": "104", "n_post": "520",
                  "n_rows": "624", "channels": "az_hp/gx/gy", "rule_class": "BUMP",
                  "rule_peak_z_g": "1.234", "rule_peak_roll_dps": "45.0",
                  "rule_peak_pitch_dps": "210.5", "rule_pitch_seq": "UD",
                  "rule_basis": "pitch", "columns": "az_hp,gx,gy",
                  "boot": "BOOT,fw=0.1.2-bench,rec=1", "crc16": self.snap.crc16}
        for k, v in expect.items():
            self.assertEqual(header.get(k), v, k)
        self.assertTrue(header["evt"].startswith("EVT,7,"))
        self.assertIn("pc_received", header)
        self.assertIn("pc_trigger_est", header)
        self.assertEqual(len(values), 624)
        # Rows are the text the firmware sent, unchanged.
        with open(path, encoding="utf-8") as fh:
            data = [ln.rstrip("\n") for ln in fh if not ln.startswith("#")]
        self.assertEqual(data[0], ",".join(self.snap.rows[0]))
        self.assertEqual(len(data), 624)

    def test_missing_evt_marked(self):
        header, _ = load_take(self.save(evt=False))
        self.assertEqual(header["rule_class"], "missing")
        self.assertEqual(header["evt"], "missing (no EVT within 5.0 s)")

    def test_numbering_and_no_reuse_after_delete(self):
        a = self.save()
        b = self.save()
        self.assertEqual((a.name, b.name), ("POTHOLE-1.csv", "POTHOLE-2.csv"))
        self.store.delete(b)
        c = self.save()
        self.assertEqual(c.name, "POTHOLE-3.csv")
        self.assertTrue((a.parent / "_deleted" / "POTHOLE-2.csv").exists())
        self.assertEqual([p.name for p in self.store.list_takes("2026-11-12_bench")],
                         ["POTHOLE-1.csv", "POTHOLE-3.csv"])

    def test_relabel(self):
        a = self.save(label="UNLABELED")
        os.utime(a, (1000.0, 1000.0))
        new = self.store.relabel(a, "SPEED_BUMP")
        self.assertEqual(new.name, "SPEED_BUMP-1.csv")
        self.assertFalse(a.exists())
        header, values = load_take(new)
        self.assertEqual(header["label"], "SPEED_BUMP")
        self.assertTrue(header["relabeled"].startswith("UNLABELED>SPEED_BUMP at "))
        self.assertEqual(len(values), 624)
        self.assertEqual(new.stat().st_mtime, 1000.0)  # Keeps recording order

    def test_relabel_same_label_is_noop(self):
        a = self.save()
        self.assertEqual(self.store.relabel(a, "POTHOLE"), a)

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            self.save(label="SPEED-BUMP")
        for bad in ("", "../x", "a/b", "has space", "_x", "CON", "x" * 70):
            with self.assertRaises(ValueError, msg=bad):
                self.save(session=bad)
        self.assertEqual(os.listdir(self.tmp.name), [])

    def test_parse_take_name(self):
        self.assertEqual(parse_take_name(Path("SPEED_BUMP-12.csv")), ("SPEED_BUMP", 12))
        self.assertIsNone(parse_take_name(Path("POTHOLE-3.dup1.csv")))
        self.assertIsNone(parse_take_name(Path(".POTHOLE-1.csv.tmp")))

    def test_no_temp_files_left(self):
        path = self.save()
        self.assertEqual(sorted(os.listdir(path.parent)), ["POTHOLE-1.csv"])

    def test_write_take_subfolders(self):
        s = "2026-11-12_bench"
        a = self.store.write_take(s, "POTHOLE", "# src_key=a\n1,2,3\n", subdir="weak")
        b = self.store.write_take(s, "NONE", "# src_key=b\n1,2,3\n", subdir="background")
        c = self.store.write_take(s, "POTHOLE", "# src_key=c\n1,2,3\n", subdir="manual")
        self.assertEqual([p.relative_to(self.tmp.name).as_posix() for p in (a, b, c)],
                         [s + "/weak/POTHOLE-1.csv", s + "/background/NONE-1.csv",
                          s + "/manual/POTHOLE-1.csv"])
        with self.assertRaises(ValueError):
            self.store.write_take(s, "NONE", "x\n")                    # Not in the session folder
        with self.assertRaises(ValueError):
            self.store.write_take(s, "POTHOLE", "x\n", subdir="background")
        with self.assertRaises(ValueError):
            self.store.write_take(s, "POTHOLE", "x\n", subdir="../other")

    def test_src_keys_and_read_header(self):
        s = "2026-11-12_bench"
        kept = self.store.write_take(s, "POTHOLE", "# src_key=k1\n# label=POTHOLE\n1,2,3\n#x=y\n")
        gone = self.store.write_take(s, "POTHOLE", "# src_key=k2\n1,2,3\n")
        self.store.delete(gone)
        self.store.write_take(s, "POTHOLE", "# src_key=k3\n1,2,3\n", subdir="weak")
        self.store.write_take(s, "POTHOLE", "# src_key=k4\n1,2,3\n", subdir="manual")
        # Deleted takes count (never cut again); raw recordings are sources.
        self.assertEqual(self.store.src_keys(s), {"k1", "k2", "k3"})
        self.assertEqual(read_header(kept), {"src_key": "k1", "label": "POTHOLE"})


if __name__ == "__main__":
    unittest.main()
