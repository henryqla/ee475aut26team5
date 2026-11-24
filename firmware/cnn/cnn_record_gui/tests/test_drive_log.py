"""
File:        test_drive_log.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for drive_log.py (request 5): the header (with the
             connection's BOOT line), board lines written byte for byte and in
             order, PC lines every second and at stop, MARK lines, a new file
             number per log (never overwriting one), and the missing-sample
             count (jumps in n, not resets), with fake clocks.
"""

# ============================================================================
# Imports
# ============================================================================

import tempfile
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
from drive_log import DriveLog, StreamStats, next_log_number

# ============================================================================
# Classes
# ============================================================================


class FakeClock:
    """A clock the test moves by hand."""

    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

# ============================================================================
# Tests
# ============================================================================


class TestDriveLog(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.drives = Path(self.tmp.name) / "s1" / "drives"
        self.wall = FakeClock(1795000000.0)
        self.mono = FakeClock(100.0)

    def new_log(self):
        return DriveLog(self.drives, {"session": "s1", "gui_commit": "abc123", "port": "COM5",
                                      "boot": helpers.LOG_BOOT}, wall=self.wall, mono=self.mono)

    def test_lines_as_received_with_pc_and_mark(self):
        log = self.new_log()
        self.assertEqual(log.path.name, "drive-1.log")
        board = [helpers.LOG_BOOT.encode(), b"#L,n,t_us", helpers.l_line(1).encode(),
                 b"EVT,0,1,2,BUMP\r", b"garbage \xff\xfe bytes", helpers.l_line(2).encode()]
        for k, raw in enumerate(board):
            log.write_board(raw)
            if raw.startswith(b"L,"):            # The reader feeds the stats each L sample
                log.stats.add(int(raw.split(b",")[1]), 0)
            if k == 2:
                self.wall.t += 1.0
                self.mono.t += 1.0
                log.tick()                       # One second: a PC line
        log.mark(1795000001234, 2, "POTHOLE")
        log.close()
        log.close()                              # Safe twice
        data = log.path.read_bytes().split(b"\n")
        header = [ln for ln in data if ln.startswith(b"#") and b"=" in ln[:30]]
        self.assertIn(b"#drive_log_format=1", header)
        self.assertIn(b"#session=s1", header)
        self.assertIn(b"#gui_commit=abc123", header)
        self.assertIn(b"#port=COM5", header)
        self.assertIn(b"#boot=" + helpers.LOG_BOOT.encode(), header)
        body = [ln for ln in data if ln and not ln.startswith(b"#")]
        self.assertEqual(body[0], b"PC,1795000000000,-1,start")
        self.assertEqual([ln for ln in body if not ln.startswith((b"PC", b"MARK"))],
                         [ln for ln in board if not ln.startswith(b"#")])   # Bytes, in order
        self.assertIn(b"#L,n,t_us", data)                # The board's own header line too
        self.assertIn(b"PC,1795000001000,1,tick", body)
        self.assertEqual(body[-2], b"MARK,1795000001234,2,POTHOLE")
        self.assertEqual(body[-1], b"PC,1795000001000,2,stop")
        self.assertEqual(log.size, log.path.stat().st_size)

    def test_new_number_never_overwrites(self):
        self.drives.mkdir(parents=True)
        (self.drives / "drive-1.log").write_text("keep")
        (self.drives / "drive-3.log").write_text("keep")
        self.assertEqual(next_log_number(self.drives), 4)
        log = self.new_log()
        log.close()
        self.assertEqual(log.path.name, "drive-4.log")
        self.assertEqual((self.drives / "drive-1.log").read_text(), "keep")

    def test_status(self):
        log = self.new_log()
        for n in range(1, 417):
            log.stats.add(n, n * 2404)
        self.mono.t += 1.0
        st = log.status(malformed_l=2)
        self.assertTrue(st.active)
        self.assertAlmostEqual(st.l_rate, 416.0)
        self.assertEqual((st.missing, st.malformed_l, st.runs), (0, 2, 1))
        log.close()
        self.assertFalse(log.status().active)


class TestStreamStats(unittest.TestCase):
    def test_jumps_resets_and_time_gaps(self):
        st = StreamStats()
        for n in range(1, 101):
            st.add(n, n * 2404)
        st.add(121, 121 * 2404)        # 20 lost
        st.add(122, 122 * 2404 + 5000)  # n continuous, but a time gap
        st.add(5, 5 * 2404)             # Reset without BOOT: a new run, not a gap
        st.add(6, 6 * 2404)
        st.new_run()                    # BOOT
        st.add(1, 2404)
        self.assertEqual((st.missing, st.timing_gaps, st.runs, st.l_lines), (20, 1, 3, 105))
        self.assertEqual(st.last_n, 1)


if __name__ == "__main__":
    unittest.main()
