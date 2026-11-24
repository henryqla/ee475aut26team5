"""
File:        test_snapshot_protocol.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B): the
             snapshot, CRC, EVT and S line tests are removed with those lines.
             The BOOT, CFG, FLT, L and ignored-line tests are kept (fed through
             LineParser instead of the assembler); HLT has 10 fields only.
             Lines from older firmware (EVT, S, SNB / SNR / SNE) are ignored.
             0.2.1: CFG lines. 0.2.0: L lines, EVT n_trig.
Description: Unit tests for snapshot_protocol.py: the line types firmware-bench
             0.1.4-bench prints, malformed L lines counted and ignored, and
             everything else ignored.
             Run from cnn_record_gui/: python -m unittest discover -s tests
"""

# ============================================================================
# Imports
# ============================================================================

import unittest

import helpers  # Sets sys.path; must come before the GUI imports
from snapshot_protocol import BootLine, ConfigLine, FaultLine, HealthLine, LineParser, LogSample

# ============================================================================
# Tests
# ============================================================================


class TestOtherLines(unittest.TestCase):
    def test_boot(self):
        out = LineParser().feed(helpers.BOOT + "\r\n")
        self.assertIsInstance(out, BootLine)
        self.assertEqual(out.settings["fw"], "0.1.4-bench")
        self.assertEqual((out.settings["vert"], out.settings["roll"], out.settings["pitch"]),
                         ("az", "gy", "gx"))
        self.assertNotIn("thr_z", out.settings)          # No thresholds any more
        self.assertEqual(out.raw, helpers.BOOT)

    def test_cfg_is_boot_settings_not_a_boot(self):
        out = LineParser().feed("CFG" + helpers.BOOT[len("BOOT"):] + "\r\n")
        self.assertIsInstance(out, ConfigLine)            # Not a BootLine: not a reboot
        again = out.as_boot()
        self.assertIsInstance(again, BootLine)
        self.assertEqual(again.raw, helpers.BOOT)
        self.assertEqual(again.settings, LineParser().feed(helpers.BOOT).settings)

    def test_hlt(self):
        out = LineParser().feed("HLT,1000,416,0,3,0,0,1,0,7,0")
        self.assertIsInstance(out, HealthLine)
        self.assertEqual(out.fields["out_q_drops"], 3)
        self.assertEqual(out.fields["serial_drops"], 7)
        self.assertNotIn("snap_drops", out.fields)
        self.assertIsNone(LineParser().feed("HLT,1000,x,0"))

    def test_flt(self):
        out = LineParser().feed("FLT,F2,IMU_LOST")
        self.assertIsInstance(out, FaultLine)
        self.assertEqual((out.code, out.name), ("F2", "IMU_LOST"))

    def test_ignored(self):
        parser = LineParser()
        for line in ("SEG,1,2,0,416,0.1,0.2,0.3,0.4", "BLE,connected,0", "#EVT,seq", "", "garbage",
                     "#L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok",
                     "#LPERF,10000,4160,180,950,123456",
                     # Lines from older firmware: ignored, not errors
                     "EVT,3,12400,29812345678,BUMP,1.234,45.0,210.5,29812,0,UD,pitch,12360",
                     "S,8,19231,0.0123,-0.0040,0.9981,0.31,-12.50,0.10,-0.0019",
                     "SNB,7,12345,416,104,520,624,az_hp/gx/gy,0.1.2-bench",
                     "SNR,0,0.1,0.2,0.3", "SNE,7,624,ABCD"):
            self.assertIsNone(parser.feed(line), line)
        self.assertEqual(parser.malformed_l, 0)


class TestLogLines(unittest.TestCase):
    def test_l_line(self):
        line = helpers.l_line(5000, z=-0.1234, roll=12.5, pitch=-61.25, ok=1)
        s = LineParser().feed(line + "\n")
        self.assertIsInstance(s, LogSample)
        self.assertEqual((s.n, s.t_us, s.ok), (5000, 12019231, True))
        self.assertEqual(s.text[8:11], ("-0.1234", "12.5000", "-61.2500"))  # Text as sent
        self.assertAlmostEqual(s.model[2], -61.25)
        self.assertAlmostEqual(s.raw[2], 0.8766)          # az = 1 g + z_hp
        self.assertEqual(s.text, tuple(line.split(",")[1:]))
        self.assertEqual(s.row_text(), line[len("L,"):])   # A clip row: the line without its tag

    def test_ok_zero(self):
        s = LineParser().feed(helpers.l_line(3, ok=0))
        self.assertFalse(s.ok)

    def test_malformed_l_counted_and_ignored(self):
        parser = LineParser()
        good = helpers.l_line(1).split(",")
        bad = [",".join(good[:-1]),                      # ok missing
               ",".join(good + ["1"]),                   # One too many
               ",".join(good[:-1] + ["2"]),              # ok not 0 or 1
               ",".join(good[:5] + ["nan"] + good[6:]),  # Not finite
               ",".join(good[:1] + ["x"] + good[2:])]    # n not an integer
        for line in bad:
            self.assertIsNone(parser.feed(line), line)
        self.assertEqual(parser.malformed_l, len(bad))


if __name__ == "__main__":
    unittest.main()
