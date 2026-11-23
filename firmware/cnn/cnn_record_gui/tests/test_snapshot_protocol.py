"""
File:        test_snapshot_protocol.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.2.0
Change:      0.2.0: the log build's L lines (text kept, malformed ones
             counted), EVT's n_trig, and an L line interrupting a snapshot
             (request 5).
Description: Unit tests for snapshot_protocol.py: the CRC variant, a valid
             firmware-format snapshot, every rejection rule in the handoff
             (missing SNE, gap in i, wrong row count, bad CRC), each lost
             snapshot counted exactly once, and the display-only lines
             (including the S sample lines for the live IMU view).
             Run from cnn_record_gui/: python -m unittest discover -s tests
"""

# ============================================================================
# Imports
# ============================================================================

import unittest

import helpers  # Sets sys.path; must come before the GUI imports
from snapshot_protocol import (S_CHANNELS, BootLine, EventLine, FaultLine, HealthLine,
                               LogSample, Rejected, SampleLine, Snapshot, SnapshotAssembler,
                               crc16_ccitt_false)

# ============================================================================
# Functions
# ============================================================================


def feed_all(asm, lines, t0=0.0):
    """
    @return Every item the assembler returns for lines, in order.
    """
    out = []
    for k, line in enumerate(lines):
        out.extend(asm.feed(line + "\n", t0 + k * 1e-4))
    return out


def new_asm():
    """
    @return An assembler with the GUI's DRAFT settings.
    """
    return SnapshotAssembler(stall_timeout_s=3.0, max_rows=20000)

# ============================================================================
# Tests
# ============================================================================


class TestCrc(unittest.TestCase):
    def test_check_value(self):
        # Handoff: ASCII "123456789" gives 29B1 for CRC-16/CCITT-FALSE.
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_chaining_equals_one_pass(self):
        self.assertEqual(crc16_ccitt_false(b"6789", crc16_ccitt_false(b"12345")), 0x29B1)


class TestValidSnapshot(unittest.TestCase):
    def test_firmware_format_accepted(self):
        lines, rows = helpers.firmware_snapshot_lines()
        out = feed_all(new_asm(), lines)
        self.assertEqual(len(out), 1)
        snap = out[0]
        self.assertIsInstance(snap, Snapshot)
        self.assertEqual((snap.seq, snap.trig_idx, snap.rate_hz), (7, 12345, 416))
        self.assertEqual((snap.n_pre, snap.n_post, snap.n_rows), (104, 520, 624))
        self.assertEqual((snap.channels, snap.fw), ("az_hp/gx/gy", "0.1.2-bench"))
        self.assertEqual(len(snap.rows), 624)
        self.assertEqual(snap.rows[0], ("-0.1000", "-573.4000", "12.2500"))  # Text as sent
        self.assertAlmostEqual(snap.values[623][1], rows[623][1], places=4)

    def test_crlf_line_endings_accepted(self):
        lines, _ = helpers.firmware_snapshot_lines()
        asm = new_asm()
        out = []
        for line in lines:
            out.extend(asm.feed(line + "\r\n", 0.0))
        self.assertIsInstance(out[0], Snapshot)

    def test_comments_and_unknown_tags_ignored_inside(self):
        lines, _ = helpers.firmware_snapshot_lines()
        lines = lines[:10] + ["#SNR,i,c0,c1,c2", "XYZ,1,2,3", ""] + lines[10:]
        out = feed_all(new_asm(), lines)
        self.assertEqual(len(out), 1)
        self.assertIsInstance(out[0], Snapshot)

    def test_two_in_a_row(self):
        a, _ = helpers.firmware_snapshot_lines(seq=1)
        b, _ = helpers.firmware_snapshot_lines(seq=2)
        out = feed_all(new_asm(), a + b)
        self.assertEqual([type(x) for x in out], [Snapshot, Snapshot])
        self.assertEqual([x.seq for x in out], [1, 2])


class TestRejections(unittest.TestCase):
    def assert_one_rejection(self, lines, reason_start):
        out = feed_all(new_asm(), lines)
        rejected = [x for x in out if isinstance(x, Rejected)]
        snaps = [x for x in out if isinstance(x, Snapshot)]
        self.assertEqual(len(snaps), 0, out)
        self.assertEqual(len(rejected), 1, out)
        self.assertTrue(rejected[0].reason.startswith(reason_start), rejected[0].reason)
        return out

    def test_missing_row_is_gap(self):
        lines, _ = helpers.firmware_snapshot_lines()
        del lines[300]
        self.assert_one_rejection(lines, "gap in i")

    def test_bad_crc(self):
        lines, _ = helpers.firmware_snapshot_lines()
        tag, seq, n, crc = lines[-1].split(",")
        lines[-1] = "%s,%s,%s,%04X" % (tag, seq, n, int(crc, 16) ^ 1)
        self.assert_one_rejection(lines, "CRC mismatch")

    def test_changed_value_fails_crc(self):
        lines, _ = helpers.firmware_snapshot_lines()
        lines[50] = lines[50][:-1] + ("1" if lines[50][-1] != "1" else "2")
        self.assert_one_rejection(lines, "CRC mismatch")

    def test_too_few_rows(self):
        lines, _ = helpers.firmware_snapshot_lines()
        lines = lines[:-25] + [lines[-1]]  # Rows 0..599 contiguous, SNE says 624
        self.assert_one_rejection(lines, "wrong row count")

    def test_sne_row_count_differs(self):
        lines, _ = helpers.firmware_snapshot_lines()
        tag, seq, n, crc = lines[-1].split(",")
        lines[-1] = ",".join((tag, seq, "600", crc))
        self.assert_one_rejection(lines, "wrong row count")

    def test_sne_seq_differs(self):
        lines, _ = helpers.firmware_snapshot_lines(seq=7)
        tag, seq, n, crc = lines[-1].split(",")
        lines[-1] = ",".join((tag, "8", n, crc))
        self.assert_one_rejection(lines, "SNE seq")

    def test_missing_sne_then_evt(self):
        lines, _ = helpers.firmware_snapshot_lines()
        out = self.assert_one_rejection(lines[:-1] + [helpers.evt_line()], "missing SNE")
        self.assertIsInstance(out[-1], EventLine)  # The EVT is still delivered

    def test_missing_sne_then_new_snb(self):
        a, _ = helpers.firmware_snapshot_lines(seq=1)
        b, _ = helpers.firmware_snapshot_lines(seq=2)
        out = feed_all(new_asm(), a[:-1] + b)
        self.assertEqual([type(x) for x in out], [Rejected, Snapshot])

    def test_device_reset_mid_snapshot(self):
        lines, _ = helpers.firmware_snapshot_lines()
        out = feed_all(new_asm(), lines[:200] + ["BOOT,fw=0.1.2-bench,rec=1"])
        self.assertEqual([type(x) for x in out], [Rejected, BootLine])

    def test_s_line_inside_snapshot_interrupts_it(self):
        # The firmware never puts an S line inside a snapshot block.
        lines, _ = helpers.firmware_snapshot_lines()
        out = feed_all(new_asm(), lines[:200] + [helpers.s_line()] + lines[200:])
        self.assertEqual(type(out[0]), Rejected)
        self.assertIn("interrupted by S", out[0].reason)
        self.assertIsInstance(out[1], SampleLine)

    def test_stall_timeout(self):
        lines, _ = helpers.firmware_snapshot_lines()
        asm = new_asm()
        feed_all(asm, lines[:100], t0=10.0)
        self.assertEqual(asm.check_stall(11.0), [])
        out = asm.check_stall(13.5)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].reason.startswith("missing SNE"))
        self.assertEqual(asm.check_stall(20.0), [])  # Counted once

    def test_abort_on_port_lost(self):
        lines, _ = helpers.firmware_snapshot_lines()
        asm = new_asm()
        feed_all(asm, lines[:100])
        self.assertEqual(len(asm.abort("USB lost")), 1)
        self.assertEqual(asm.abort("USB lost"), [])

    def test_rows_without_snb_counted_once(self):
        lines, _ = helpers.firmware_snapshot_lines()
        self.assert_one_rejection(lines[300:], "missing SNB")

    def test_malformed_snb_counted_once(self):
        lines, _ = helpers.firmware_snapshot_lines()
        lines[0] = "SNB,7,12345,416,104,520"  # Fields missing
        self.assert_one_rejection(lines, "malformed SNB")

    def test_inconsistent_snb_counted_once(self):
        lines, _ = helpers.firmware_snapshot_lines()
        lines[0] = lines[0].replace(",624,", ",625,")  # n_rows != n_pre + n_post
        self.assert_one_rejection(lines, "inconsistent SNB")

    def test_non_finite_value(self):
        lines, _ = helpers.firmware_snapshot_lines()
        lines[10] = "SNR,9,nan,0.0000,0.0000"
        self.assert_one_rejection(lines, "malformed SNR")


class TestOtherLines(unittest.TestCase):
    def test_evt(self):
        out = new_asm().feed(helpers.evt_line(seq=3, cls="POTHOLE"), 0.0)
        self.assertEqual(len(out), 1)
        evt = out[0]
        self.assertIsInstance(evt, EventLine)
        self.assertEqual(evt.seq, 3)
        self.assertEqual(evt.fields["class"], "POTHOLE")
        self.assertEqual(evt.fields["peak_pitch_dps"], "210.5")
        self.assertEqual(evt.fields["basis"], "pitch")

    def test_boot(self):
        out = new_asm().feed("BOOT,fw=0.1.2-bench,thr_z=1.000,rearm_frac=0.50,rec=1", 0.0)
        self.assertEqual(out[0].settings["rec"], "1")
        self.assertEqual(out[0].settings["thr_z"], "1.000")

    def test_hlt_product_and_record(self):
        asm = new_asm()
        product = asm.feed("HLT,1000,416,0,0,0,0,1,0,0,0", 0.0)[0]
        record = asm.feed("HLT,2000,832,0,0,0,0,2,0,0,0,3", 0.0)[0]
        self.assertIsInstance(product, HealthLine)
        self.assertNotIn("snap_drops", product.fields)
        self.assertEqual(record.fields["snap_drops"], 3)

    def test_flt(self):
        out = new_asm().feed("FLT,F2,IMU_LOST", 0.0)[0]
        self.assertIsInstance(out, FaultLine)
        self.assertEqual((out.code, out.name), ("F2", "IMU_LOST"))

    def test_s_line(self):
        out = new_asm().feed(helpers.s_line(n=4096, t_us=9846153) + "\r\n", 0.0)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertIsInstance(s, SampleLine)
        self.assertEqual((s.n, s.t_us), (4096, 9846153))
        self.assertEqual(len(s.values), len(S_CHANNELS))
        v = dict(zip(S_CHANNELS, s.values))
        self.assertAlmostEqual(v["az"], 0.9981)
        self.assertAlmostEqual(v["gy"], -12.5)
        self.assertAlmostEqual(v["z_hp"], -0.0019)

    def test_malformed_s_line_ignored(self):
        asm = new_asm()
        for line in ("S,1,2,0.1,0.2,0.3,1,2,3",            # One value missing
                     "S,1,2,0.1,0.2,0.3,1,2,3,0.4,5",      # One too many
                     "S,x,2,0.1,0.2,0.3,1,2,3,0.4",        # n not an integer
                     "S,1,2,0.1,nan,0.3,1,2,3,0.4",        # Not finite
                     "S,1,2,0.1,,0.3,1,2,3,0.4"):          # Empty field
            self.assertEqual(asm.feed(line, 0.0), [], line)

    def test_ignored(self):
        asm = new_asm()
        for line in ("SEG,1,2,0,416,0.1,0.2,0.3,0.4", "BLE,connected,0", "#EVT,seq",
                     "#S,n,t_us,ax,ay,az,gx,gy,gz,z_hp", "", "garbage",
                     "#L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok",
                     "#LPERF,10000,4160,180,950,123456"):
            self.assertEqual(asm.feed(line, 0.0), [])


class TestLogLines(unittest.TestCase):
    def test_l_line(self):
        line = helpers.l_line(5000, z=-0.1234, roll=12.5, pitch=-61.25, ok=1)
        out = new_asm().feed(line + "\n", 0.0)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertIsInstance(s, LogSample)
        self.assertEqual((s.n, s.t_us, s.ok), (5000, 12019231, True))
        self.assertEqual(s.model_text(), ("-0.1234", "12.5000", "-61.2500"))  # Text as sent
        self.assertAlmostEqual(s.model[2], -61.25)
        self.assertAlmostEqual(s.raw[2], 0.8766)          # az = 1 g + z_hp
        self.assertEqual(s.text, tuple(line.split(",")[1:]))

    def test_ok_zero(self):
        s = new_asm().feed(helpers.l_line(3, ok=0), 0.0)[0]
        self.assertFalse(s.ok)

    def test_malformed_l_counted_and_ignored(self):
        asm = new_asm()
        good = helpers.l_line(1).split(",")
        bad = [",".join(good[:-1]),                      # ok missing
               ",".join(good + ["1"]),                   # One too many
               ",".join(good[:-1] + ["2"]),              # ok not 0 or 1
               ",".join(good[:5] + ["nan"] + good[6:]),  # Not finite
               ",".join(good[:1] + ["x"] + good[2:])]    # n not an integer
        for line in bad:
            self.assertEqual(asm.feed(line, 0.0), [], line)
        self.assertEqual(asm.malformed_l, len(bad))

    def test_l_line_inside_snapshot_interrupts_it(self):
        lines, _ = helpers.firmware_snapshot_lines()
        out = feed_all(new_asm(), lines[:200] + [helpers.l_line(9)] + lines[200:])
        self.assertEqual(type(out[0]), Rejected)
        self.assertIn("interrupted by L", out[0].reason)
        self.assertIsInstance(out[1], LogSample)

    def test_evt_n_trig(self):
        evt = new_asm().feed(helpers.evt_line(seq=4, n_trig=12360), 0.0)[0]
        self.assertEqual(evt.fields["n_trig"], "12360")
        self.assertEqual(evt.fields["basis"], "pitch")
        old = new_asm().feed(helpers.evt_line(seq=4), 0.0)[0]   # 0.1.2-bench: 12 fields
        self.assertNotIn("n_trig", old.fields)


if __name__ == "__main__":
    unittest.main()
