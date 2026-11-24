"""
File:        test_cut_windows.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for cut_windows.py (request 6, with request 8):
             CUT-1 (a take cut at each EVT of the simulated log build equals
             its snapshot, row for row as text, with the same CRC), labels
             from marks, CUT-3 (an event at the start of a log or across a
             gap is skipped; a mark with no EVT becomes a marker_only or weak
             take or is listed for review), raw manual recordings, CUT-4
             (a second run adds nothing; different settings are refused),
             EVTs and marks covered by a manual recording (owner,
             2026-11-22), and background windows (count, distance, seed).
             Standard library only; needs no TensorFlow.
"""

# ============================================================================
# Imports
# ============================================================================

import math
import tempfile
import unittest
from pathlib import Path

import helpers  # noqa: F401  (sets sys.path)
import cnn_config as cfg
import cut_windows as cw
from drive_log import DriveLog
from manual_record import ManualRecorder, raw_recording_text, save_capture
from sim_source import SimDevice, SimDisconnect
from snapshot_protocol import Snapshot, SnapshotAssembler, parse_other_line
from take_store import TakeStore, header_text, load_take, read_header

# ============================================================================
# Constants and line builders (the firmware's formats)
# ============================================================================

SESSION = "2026-11-22_bench"
EPOCH0 = 1795000000000      # Laptop time of n = 0, ms

LOG_BOOT = ("BOOT,fw=0.1.3-bench,odr=416,acc_fs=16,gyr_fs=500,thr_z=1.000,thr_roll=60.0,"
            "thr_pitch=60.0,lockout_ms=1250,rearm_frac=0.50,rearm_hold_ms=200,hpf_hz=0.50,"
            "print_every=8,settle_ms=2000,ble=RoadQuality,rec=0,log=1,l_rate=416,l_dec=4,"
            "log_tx_buf=32768,log_out_q=256,log_perf_ms=10000")


def l_line(n, z=0.0, ok=1):
    """@return An L line as serial_out.cpp prints it."""
    return "L,%d,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d" % (
        n, int(round(n * 1e6 / 416)), 0.0012, -0.0021, 1.0 + z, 0.4, -0.3, 0.2, z, 0.0, 0.0, ok)


def bump(n0, amp):
    """@return {n: z_hp} of an event whose first sample is n0."""
    return {n0 + k: amp * math.exp(-k / 25.0) * (1 if k % 2 == 0 else -1) for k in range(60)}


def evt_line(seq, n_trig):
    """@return An EVT line (0.1.3-bench, n_trig last); the peak 2 samples later."""
    return "EVT,%d,%d,%d,BUMP,1.200,10.0,20.0,%d,0,UD,pitch,%d" % (
        seq, n_trig + 2, int((n_trig + 2) * 1e6 / 416), (n_trig + 2) * 1000 // 416, n_trig)


def epoch_at(n):
    """@return Laptop time of sample n, ms."""
    return EPOCH0 + n * 1000 // 416


def write_log(path, n_last, events=None, after=None, skip=(), header_boot=LOG_BOOT):
    """
    Writes a drive log as drive_log.py would.
    @param path         The file.
    @param n_last       Samples n = 1..n_last.
    @param events       {n: z_hp}.
    @param after        {n: [lines]} written after sample n (EVT, MARK).
    @param skip         Sample counts lost on the way (jumps in n).
    @param header_boot  The header's BOOT line, or None.
    """
    events, after = events or {}, after or {}
    lines = ["#drive_log_format=1", "#session=%s" % SESSION, "#gui_commit=abc123",
             "#port=test", "#pc_start=x", "#pc_start_epoch_ms=%d" % EPOCH0,
             "#boot=%s" % (header_boot or "missing (not seen on this connection)"),
             "PC,%d,-1,start" % EPOCH0]
    for n in range(1, n_last + 1):
        if n not in skip:
            lines.append(l_line(n, events.get(n, ((n * 7) % 13 - 6) * 0.0011)))
        if n % 416 == 0:
            lines.append("PC,%d,%d,tick" % (epoch_at(n), n))
        lines.extend(after.get(n, []))
    lines.append("PC,%d,%d,stop" % (epoch_at(n_last), n_last))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def data_rows(path):
    """@return A take's data rows as text."""
    with open(path, encoding="utf-8") as fh:
        return [ln.rstrip("\n") for ln in fh if not ln.startswith("#")]

# ============================================================================
# Tests
# ============================================================================


class CutTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.store = TakeStore(self.data)
        self.drives = self.data / SESSION / cfg.DRIVES_DIR_NAME
        self.drives.mkdir(parents=True)

    def cut(self, background=0):
        return cw.run_cut(self.data, SESSION, background=background)

    def made(self, rep, source=None):
        return [(p, label) for p, label, s, _a in rep.made if source is None or s == source]


class TestCut1(CutTestCase):
    def test_sim_takes_equal_snapshots(self):
        # The simulated log build, test mode: every event's snapshot too.
        dev = SimDevice(build="log", with_snapshots=True, bad_every=0, reset_every=0,
                        disconnect_every=0, gap_every=0)
        log = DriveLog(self.drives, {"session": SESSION, "gui_commit": "abc123", "port": "sim"})
        asm = SnapshotAssembler(3.0, 20000)
        snaps = {}
        for _delay, item in dev.stream():
            if len(dev.plan) > 6:
                break
            log.write_board(item.encode("utf-8"))
            for x in asm.feed(item, 0.0):
                if isinstance(x, Snapshot):
                    snaps[x.trig_idx] = x
        log.close()
        rep = self.cut()
        evt_takes = self.made(rep, "evt")
        self.assertEqual(len(evt_takes), 6, rep.skipped)
        for rel, label in evt_takes:
            path = self.data / SESSION / rel
            header = read_header(path)
            snap = snaps[int(header["n_trig"])]
            self.assertEqual(data_rows(path), [",".join(r) for r in snap.rows])   # As text
            self.assertEqual(header["crc16"], snap.crc16)
            self.assertEqual((header["align"], header["trig_idx"]),
                             ("trigger", str(snap.trig_idx)))
            self.assertEqual(label, cfg.UNLABELED)      # No marks in this log
        self.assertEqual(len(rep.unlabeled_evts), 6)
        self.assertTrue((self.data / SESSION / cfg.CUT_REPORT_NAME).exists())


class TestLabelsAndBoundaries(CutTestCase):
    def test_label_from_the_last_mark(self):
        events = dict(bump(2000, 1.3))
        events.update(bump(5000, 1.3))
        after = {2519: [evt_line(0, 2000)], 2300: ["MARK,%d,2300,POTHOLE" % epoch_at(2300)],
                 2600: ["MARK,%d,2600,SPEED_BUMP" % epoch_at(2600)],
                 5519: [evt_line(1, 5000)]}
        write_log(self.drives / "drive-1.log", 7000, events, after)
        rep = self.cut()
        takes = {read_header(self.data / SESSION / p)["n_trig"]: (p, label)
                 for p, label in self.made(rep, "evt")}
        self.assertEqual(takes["2000"][1], "SPEED_BUMP")
        h = read_header(self.data / SESSION / takes["2000"][0])
        self.assertEqual((h["marks"], h["label_from"], h["rule_class"], h["n_peak"]),
                         ("POTHOLE@2300;SPEED_BUMP@2600", "mark", "BUMP", "2002"))
        self.assertEqual(takes["5000"][1], cfg.UNLABELED)
        self.assertEqual(len(rep.unlabeled_evts), 1)
        self.assertFalse(self.made(rep, "marker_only"))  # Both marks labeled the EVT

    def test_cut3_boundaries(self):
        events = dict(bump(60, 1.3))
        events.update(bump(3000, 1.3))
        events.update(bump(5800, 0.7))       # Below the threshold, above MISS_FRAC
        events.update(bump(8700, 0.3))       # Weak: above the floor only
        after = {579: [evt_line(0, 60)],                       # At the very start
                 3519: [evt_line(1, 3000)],                    # A gap across it (skip)
                 6200: ["MARK,%d,6200,POTHOLE" % epoch_at(6200)],
                 9000: ["MARK,%d,9000,SPEED_BUMP" % epoch_at(9000)],
                 11000: ["MARK,%d,11000,POTHOLE" % epoch_at(11000)]}   # Nothing there
        write_log(self.drives / "drive-1.log", 13000, events, after, skip={3200})
        rep = self.cut()
        reasons = dict(rep.skipped)
        self.assertIn("too few rows before", reasons["drive-1.log run 1 EVT seq 0"])
        self.assertIn("jump in n", reasons["drive-1.log run 1 EVT seq 1"])
        marker = self.made(rep, "marker_only")
        self.assertEqual(len(marker), 2)
        by_label = {label: p for p, label in marker}
        h = read_header(self.data / SESSION / by_label["POTHOLE"])
        self.assertEqual((h["align"], h["align_n"], h["source"]), ("miss_frac", "5800",
                                                                   "marker_only"))
        self.assertTrue(by_label["SPEED_BUMP"].startswith("weak/"))
        hw = read_header(self.data / SESSION / by_label["SPEED_BUMP"])
        self.assertEqual((hw["align"], hw["n_peak"], hw["peak_gap"]), ("peak", "8700", "0"))
        self.assertEqual(len(rep.review), 1)
        self.assertIn("MARK POTHOLE at n 11000", rep.review[0][0])

    def test_log_without_boot(self):
        write_log(self.drives / "drive-1.log", 4000, bump(2000, 1.3),
                  {2519: [evt_line(0, 2000)]}, header_boot=None)
        rep = self.cut()
        self.assertFalse(rep.made)
        self.assertIn("no BOOT line", rep.skipped[0][1])


class TestManualAndCoverage(CutTestCase):
    def raw_recording(self, events, press_n=3100, label="POTHOLE", cut=False):
        """
        A raw manual recording around press_n, made by ManualRecorder; with
        cut=True also its take (as manual Record saves both).
        @return The Capture.
        """
        rows = [parse_other_line(l_line(n, events.get(n, ((n * 7) % 13 - 6) * 0.0011)))
                for n in range(1, 6001)]
        rec = ManualRecorder()
        rec.feed(rows[:press_n])
        self.assertEqual(rec.press(label, epoch_at(press_n), LOG_BOOT), "")
        cap = rec.feed(rows[press_n:])
        if cut:
            save_capture(self.store, SESSION, cap, "abc123")
        else:
            self.store.write_take(SESSION, label, raw_recording_text(cap, SESSION, "abc123"),
                                  subdir=cfg.MANUAL_DIR_NAME)
        return cap

    def test_raw_recordings_weak_and_review(self):
        self.raw_recording(bump(3500, 0.3), label="SPEED_BUMP")                # Weak
        self.raw_recording(bump(3500, 0.1), press_n=3150, label="POTHOLE")     # Nothing
        rep = self.cut()
        manual = self.made(rep, "manual")
        self.assertEqual([(p, label) for p, label in manual],
                         [("weak/SPEED_BUMP-1.csv", "SPEED_BUMP")])
        self.assertEqual(len(rep.review), 1)
        self.assertIn("manual/POTHOLE-1.csv", rep.review[0][0])

    def test_recording_already_cut_by_the_gui_is_skipped(self):
        self.raw_recording(bump(3500, 1.3), cut=True)
        rep = self.cut()
        self.assertFalse(rep.made)
        self.assertEqual(rep.already, 1)

    def test_evt_and_mark_covered_by_a_manual_recording(self):
        events = dict(bump(3000, 1.3))
        events.update(bump(8000, 1.3))
        cap = self.raw_recording(events, press_n=3100, cut=True)
        after = {3100: ["MARK,%d,3100,POTHOLE" % epoch_at(3100),
                        "MARK,%d,%d,manual_POTHOLE" % (cap.press_epoch_ms, cap.press_n)],
                 3519: [evt_line(0, 3000)], 8519: [evt_line(1, 8000)]}
        write_log(self.drives / "drive-1.log", 10000, events, after)
        rep = self.cut()
        self.assertEqual(len(rep.covered), 2, rep.covered)   # The EVT at 3000 and the mark
        evt_takes = self.made(rep, "evt")
        self.assertEqual(len(evt_takes), 1)
        self.assertEqual(read_header(self.data / SESSION / evt_takes[0][0])["n_trig"], "8000")


class TestCut4(CutTestCase):
    def log(self):
        events = dict(bump(3000, 1.3))
        events.update(bump(6000, 0.7))
        write_log(self.drives / "drive-1.log", 30000, events,
                  {3519: [evt_line(0, 3000)], 6300: ["MARK,%d,6300,POTHOLE" % epoch_at(6300)]})

    def test_second_run_adds_nothing(self):
        self.log()
        first = self.cut(background=3)
        self.assertEqual(len(first.made), 1 + 1 + 3)        # EVT, marker_only, background
        listing = sorted(p.relative_to(self.data).as_posix()
                         for p in (self.data / SESSION).rglob("*.csv"))
        second = self.cut(background=3)
        self.assertEqual(second.made, [])
        self.assertEqual(second.already, 2)
        self.assertEqual(listing, sorted(p.relative_to(self.data).as_posix()
                                         for p in (self.data / SESSION).rglob("*.csv")))

    def test_different_settings_refused(self):
        self.log()
        old = [("label", "POTHOLE"), ("rate_hz", 416), ("n_pre", 104), ("n_post", 520),
               ("n_rows", 624), ("channels", "az_hp/gx/gy"), ("fw", "0.1.2-bench")]
        self.store.write_take(SESSION, "POTHOLE", header_text(old) + "0,0,0\n")
        rep = self.cut()
        self.assertFalse(rep.made)
        self.assertEqual(len(rep.refused), 2)
        self.assertIn("fw=0.1.3-bench", rep.refused[0][1])


class TestBackground(CutTestCase):
    def test_count_distance_and_seed(self):
        after = {12519: [evt_line(0, 12000)], 12300: ["MARK,%d,12300,POTHOLE" % epoch_at(12300)]}
        write_log(self.drives / "drive-1.log", 416 * 60, bump(12000, 1.3), after)
        rep = self.cut(background=5)
        bg = self.made(rep, "background")
        self.assertEqual(len(bg), 5)
        clear = int(cfg.BACKGROUND_CLEAR_S * 416)
        starts = []
        for rel, label in bg:
            self.assertTrue(rel.startswith("background/NONE-"))
            path = self.data / SESSION / rel
            h, values = load_take(path)
            self.assertEqual(len(values), 624)
            n0 = int(h["align_n"]) - 104
            starts.append(n0)
            for p in (12000, 12300):
                self.assertTrue(p <= n0 - clear or p >= n0 + 623 + clear, (n0, p))
        starts.sort()
        self.assertTrue(all(b - a >= 624 for a, b in zip(starts, starts[1:])))   # No overlap
        # The same log in another session: the same windows (seed 475).
        other = self.data / "other" / cfg.DRIVES_DIR_NAME
        other.mkdir(parents=True)
        (other / "drive-1.log").write_bytes((self.drives / "drive-1.log").read_bytes())
        rep2 = cw.run_cut(self.data, "other", background=5, write_report=False)
        self.assertEqual(sorted(int(read_header(self.data / "other" / p)["align_n"]) - 104
                                for p, _l in self.made(rep2, "background")), starts)


if __name__ == "__main__":
    unittest.main()
