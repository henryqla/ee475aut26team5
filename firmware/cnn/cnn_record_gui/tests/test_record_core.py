"""
File:        test_record_core.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Description: Unit tests for record_core.py: Arm saves one snapshot, keep armed
             saves all, disarmed snapshots are not saved, EVT pairing in
             either order, the 5 s timeout, BOOT / reconnect clearing, and
             quick labels before and after a take is written.
"""

# ============================================================================
# Imports
# ============================================================================

import tempfile
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
from record_core import RecordController
from snapshot_protocol import BootLine, Rejected, SnapshotAssembler
from take_store import TakeStore, load_take

# ============================================================================
# Constants
# ============================================================================

W0 = 1.79e9  # A realistic wall time (time.time()) for the snapshot arrival times

# ============================================================================
# Functions
# ============================================================================


def parse(lines):
    """
    @return Items parsed from lines.
    """
    asm = SnapshotAssembler(3.0, 20000)
    out = []
    for line in lines:
        out.extend(asm.feed(line, 0.0))
    return out


def snap(seq):
    """
    @return A firmware-format Snapshot with this seq.
    """
    return parse(helpers.firmware_snapshot_lines(seq=seq)[0])[0]


def evt(seq, cls="POTHOLE"):
    """
    @return An EventLine with this seq.
    """
    return parse([helpers.evt_line(seq=seq, cls=cls)])[0]

# ============================================================================
# Tests
# ============================================================================


class TestRecordController(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TakeStore(Path(self.tmp.name))
        self.saved = []
        self.errors = []
        self.c = RecordController(self.store, "abc123", timeout_s=5.0,
                                  on_saved=lambda p, cl: self.saved.append(p),
                                  on_error=self.errors.append)
        self.c.session = "s1"
        self.c.label = "POTHOLE"

    def tearDown(self):
        self.tmp.cleanup()

    def test_disarmed_not_saved(self):
        self.c.on_snapshot(snap(0), W0 + 0.0, 0.0)
        self.c.on_event(evt(0), 0.1)
        self.c.tick(10.0)
        self.assertEqual(self.saved, [])
        self.assertEqual(self.c.counters.not_armed, 1)
        self.assertEqual(self.c.last_rule_class, "POTHOLE")  # Still shown

    def test_arm_saves_one_then_disarms(self):
        self.c.arm()
        self.c.on_snapshot(snap(0), W0 + 0.0, 0.0)
        self.assertFalse(self.c.armed)
        self.c.on_event(evt(0), 0.1)
        self.c.on_snapshot(snap(1), W0 + 2.0, 2.0)
        self.c.on_event(evt(1), 2.1)
        self.assertEqual([p.name for p in self.saved], ["POTHOLE-1.csv"])
        self.assertEqual(load_take(self.saved[0])[0]["seq"], "0")

    def test_evt_first_then_snapshot(self):
        self.c.arm()
        self.c.on_event(evt(4, "BUMP"), 0.0)
        self.c.on_snapshot(snap(4), W0 + 0.2, 0.2)
        self.assertEqual(len(self.saved), 1)
        self.assertEqual(load_take(self.saved[0])[0]["rule_class"], "BUMP")

    def test_keep_armed_saves_all(self):
        self.c.keep_armed = True
        for s in range(3):
            self.c.on_snapshot(snap(s), W0 + s, s)
            self.c.on_event(evt(s), s + 0.1)
        self.assertEqual(len(self.saved), 3)
        self.assertTrue(self.c.keep_armed)

    def test_timeout_saves_without_evt(self):
        self.c.arm()
        self.c.on_snapshot(snap(0), W0 + 0.0, 0.0)
        self.c.tick(4.9)
        self.assertEqual(self.saved, [])
        self.c.tick(5.0)
        self.assertEqual(len(self.saved), 1)
        header = load_take(self.saved[0])[0]
        self.assertEqual(header["rule_class"], "missing")
        self.assertIn("no EVT within 5.0 s", header["evt"])
        self.assertEqual(self.c.counters.saved_without_evt, 1)

    def test_evt_without_snapshot_counted(self):
        self.c.on_event(evt(9), 0.0)
        self.c.tick(5.0)
        self.assertEqual(self.c.counters.evt_without_snap, 1)
        self.assertEqual(self.saved, [])

    def test_boot_clears_and_saves_claimed(self):
        self.c.arm()
        self.c.on_snapshot(snap(5), W0 + 0.0, 0.0)
        self.c.on_event(evt(6), 0.1)                    # Waiting EVT, other seq
        self.c.on_boot(BootLine({"fw": "x"}, "BOOT,fw=x"))
        self.assertEqual(len(self.saved), 1)            # Claimed snapshot kept
        self.assertEqual(self.c.counters.evt_without_snap, 1)
        # After the reboot seq 5 again must not pair with anything old.
        self.c.arm()
        self.c.on_snapshot(snap(5), W0 + 1.0, 1.0)
        self.c.on_event(evt(5, "BUMP"), 1.1)
        self.assertEqual(load_take(self.saved[1])[0]["rule_class"], "BUMP")
        self.assertEqual(load_take(self.saved[1])[0]["boot"], "BOOT,fw=x")

    def test_reconnect_clears(self):
        self.c.arm()
        self.c.on_snapshot(snap(0), W0 + 0.0, 0.0)
        self.c.on_reconnect()
        self.assertEqual(len(self.saved), 1)
        self.assertIn("USB reconnected", load_take(self.saved[0])[0]["evt"])
        self.assertIsNone(self.c.boot)

    def test_quick_label_before_save(self):
        self.c.label = "UNLABELED"
        self.c.keep_armed = True
        self.c.on_snapshot(snap(0), W0 + 0.0, 0.0)           # EVT not here yet
        self.c.quick_label("SPEED_BUMP")
        self.c.on_event(evt(0), 0.1)
        self.assertEqual([p.name for p in self.saved], ["SPEED_BUMP-1.csv"])

    def test_quick_label_after_save(self):
        self.c.label = "UNLABELED"
        self.c.keep_armed = True
        self.c.on_snapshot(snap(0), W0 + 0.0, 0.0)
        self.c.on_event(evt(0), 0.1)
        new = self.c.quick_label("POTHOLE")
        self.assertEqual(new.name, "POTHOLE-1.csv")
        self.assertEqual(load_take(new)[0]["label"], "POTHOLE")
        self.assertFalse(self.saved[0].exists())

    def test_rejected_and_health(self):
        self.c.on_rejected(Rejected("CRC mismatch"))
        self.assertEqual(self.c.counters.rejected, 1)
        self.c.on_health(parse(["HLT,1,2,0,0,0,0,0,0,0,0,4"])[0])
        self.assertEqual(self.c.counters.device_snap_drops, 4)

    def test_save_error_reported_not_raised(self):
        self.c.session = "bad name"                    # Invalid folder name
        self.c.arm()
        self.c.on_snapshot(snap(0), W0 + 0.0, 0.0)
        self.c.on_event(evt(0), 0.1)
        self.assertEqual(self.c.counters.save_errors, 1)
        self.assertEqual(len(self.errors), 1)


if __name__ == "__main__":
    unittest.main()
