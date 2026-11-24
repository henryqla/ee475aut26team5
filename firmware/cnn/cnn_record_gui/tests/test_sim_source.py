"""
File:        test_sim_source.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.2.1
Change:      0.2.1: the CFG line repeats the BOOT fields every CFG_PERIOD_S.
             0.2.0: the log build (request 5): L lines every sample, ok=0
             during settle, EVT n_trig at the crossing, the missing-sample
             count equal to the injected jumps (DRV-1), and the test mode's
             snapshot equal to the L lines' model channels as text.
Description: Runs the simulated device through the parser and record logic
             (as GUI-3 / GUI-4 do on screen) and checks that every good
             snapshot is accepted and every injected fault is rejected or
             counted as planned, with seq restarting after resets.
"""

# ============================================================================
# Imports
# ============================================================================

import tempfile
import unittest
from pathlib import Path

import helpers  # Sets sys.path; must come before the GUI imports
import cnn_config as cfg
from drive_log import StreamStats
from record_core import RecordController
from sim_source import SimDevice, SimDisconnect
from snapshot_protocol import (BootLine, ConfigLine, EventLine, HealthLine, LogSample,
                               Rejected, SampleLine, Snapshot, SnapshotAssembler)
from take_store import TakeStore, load_take

# ============================================================================
# Tests
# ============================================================================


class TestSimDevice(unittest.TestCase):
    def test_faults_detected_as_planned(self):
        n_events = 30
        dev = SimDevice(seed=475, event_period_s=3.0, bad_every=4, reset_every=9,
                        disconnect_every=13, disconnect_s=2.0)
        asm = SnapshotAssembler(3.0, 20000)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        c = RecordController(TakeStore(Path(tmp.name)), "abc123", timeout_s=5.0)
        c.session = "sim_test"
        c.keep_armed = True
        now = 0.0
        snaps, rejected, boots = [], [], 0
        for delay, item in dev.stream():
            # plan grows just before an event's first line: stop before
            # feeding anything from event n_events + 1.
            if len(dev.plan) > n_events:
                break
            now += delay
            if isinstance(item, SimDisconnect):
                rejected += [r for r in asm.abort("USB lost")]
                c.on_reconnect()
                now += item.duration_s
                continue
            for x in asm.feed(item, now) + asm.check_stall(now):
                if isinstance(x, Snapshot):
                    snaps.append(x)
                    c.on_snapshot(x, 1.79e9 + now, now)
                elif isinstance(x, Rejected):
                    rejected.append(x)
                    c.on_rejected(x)
                elif isinstance(x, EventLine):
                    c.on_event(x, now)
                elif isinstance(x, BootLine):
                    boots += 1
                    c.on_boot(x)
                elif isinstance(x, HealthLine):
                    c.on_health(x)
            c.tick(now)
        c.tick(now + 10.0)

        plan = dev.plan[:n_events]
        kinds = [k for _, _, k in plan]
        n_ok = kinds.count("ok")
        n_rejectable = sum(kinds.count(k) for k in
                           ("missing_row", "bad_crc", "wrong_length", "missing_sne",
                            "disconnect"))
        self.assertEqual(len(snaps), n_ok)
        self.assertEqual(len(rejected), n_rejectable)
        self.assertGreater(kinds.count("device_drop"), 0)
        self.assertGreater(kinds.count("disconnect"), 0)
        self.assertEqual(boots, 1 + n_events // 9 + kinds.count("disconnect"))
        # Every good snapshot was saved, with its EVT (the sim always sends one).
        self.assertEqual(c.counters.saved, n_ok)
        self.assertEqual(c.counters.saved_without_evt, 0)
        # EVTs without a snapshot: device drops plus the rejected ones.
        self.assertEqual(c.counters.evt_without_snap,
                         kinds.count("device_drop") + n_rejectable - kinds.count("disconnect"))
        for p in TakeStore(Path(tmp.name)).list_takes("sim_test"):
            header, values = load_take(p)
            self.assertEqual(len(values), 624)
            self.assertTrue(header["fw"].startswith("sim-"))
        # seq restarts at 0 after each reset.
        self.assertIn(0, [seq for k, seq, _ in plan if k > 1])

    def test_s_lines_for_the_live_view(self):
        n_events = 6
        dev = SimDevice(seed=475, event_period_s=3.0, bad_every=0, reset_every=3,
                        disconnect_every=0)
        asm = SnapshotAssembler(3.0, 20000)
        samples, snaps, boots = [], 0, 0
        now = 0.0
        for delay, item in dev.stream():
            if len(dev.plan) > n_events:
                break
            now += delay
            for x in asm.feed(item, now) + asm.check_stall(now):
                if isinstance(x, SampleLine):
                    samples.append((boots, x))
                elif isinstance(x, BootLine):
                    boots += 1
                elif isinstance(x, Snapshot):
                    snaps += 1
                elif isinstance(x, Rejected):
                    self.fail(x.reason)    # S lines must never land inside a snapshot
        self.assertEqual(snaps, n_events)
        self.assertEqual(boots, 1 + n_events // 3)
        # Every 8th sample, 8 samples (8 / 416 s) apart, until the next boot.
        pairs = 0
        for (b0, a), (b1, b) in zip(samples, samples[1:]):
            self.assertEqual(b.n % 8, 0)
            if b0 == b1:
                pairs += 1
                self.assertEqual(b.n - a.n, 8)
                self.assertAlmostEqual(b.t_us - a.t_us, 8e6 / 416, delta=1)
        self.assertGreater(pairs, 6 * 150)        # ~156 S lines per 3 s event
        # Gravity on az, and the events show up in z_hp.
        self.assertAlmostEqual(sum(x.values[2] for _, x in samples) / len(samples), 1.0,
                               delta=0.05)
        self.assertGreater(max(abs(x.values[6]) for _, x in samples), 0.5)

    def test_record_build_evt_has_n_trig(self):
        dev = SimDevice(bad_every=0, reset_every=0, disconnect_every=0)
        asm = SnapshotAssembler(3.0, 20000)
        evts, snaps = {}, {}
        for _delay, item in dev.stream():
            if len(dev.plan) > 3:
                break
            for x in asm.feed(item, 0.0):
                if isinstance(x, EventLine):
                    evts[x.seq] = x
                elif isinstance(x, Snapshot):
                    snaps[x.seq] = x
        self.assertEqual(set(evts), set(snaps))
        for seq, evt in evts.items():
            self.assertEqual(int(evt.fields["n_trig"]), snaps[seq].trig_idx)


class TestSimLogBuild(unittest.TestCase):
    def run_log(self, n_events, **kwargs):
        """
        Feeds the log build's stream through the parser and StreamStats, as
        the reader thread does.
        @return (device, items, stats).
        """
        dev = SimDevice(seed=475, event_period_s=3.0, build="log", **kwargs)
        asm = SnapshotAssembler(3.0, 20000)
        stats = StreamStats()
        items = []
        for _delay, item in dev.stream():
            if len(dev.plan) > n_events:
                break
            if isinstance(item, SimDisconnect):
                items.append(item)
                continue
            for x in asm.feed(item, 0.0):
                items.append(x)
                if isinstance(x, LogSample):
                    stats.add(x.n, x.t_us)
                elif isinstance(x, BootLine):
                    stats.new_run()
                elif isinstance(x, Rejected):
                    self.fail(x.reason)
        return dev, items, stats

    def test_l_lines_settle_and_n_trig(self):
        dev, items, stats = self.run_log(8, reset_every=0, disconnect_every=0, gap_every=0)
        boot = [x for x in items if isinstance(x, BootLine)]
        self.assertEqual(boot[0].settings["log"], "1")
        self.assertEqual(boot[0].settings["rec"], "0")
        self.assertFalse([x for x in items if isinstance(x, (SampleLine, Snapshot))])
        hlt = [x for x in items if isinstance(x, HealthLine)]
        self.assertNotIn("snap_drops", hlt[0].fields)
        rows = [x for x in items if isinstance(x, LogSample)]
        self.assertEqual([s.n for s in rows], list(range(1, len(rows) + 1)))   # Every sample
        settle = int(cfg.SIM_SETTLE_S * 416)
        self.assertEqual([s.ok for s in rows[:settle + 1]], [False] * settle + [True])
        by_n = {s.n: s for s in rows}
        evts = [x for x in items if isinstance(x, EventLine)]
        self.assertEqual(len(evts), 8)
        for evt in evts:
            n_trig = int(evt.fields["n_trig"])
            self.assertGreaterEqual(abs(by_n[n_trig].model[0]), 1.0)        # Crosses there
            self.assertTrue(all(abs(by_n[n].model[0]) < 0.5 for n in range(n_trig - 104,
                                                                          n_trig)))
        self.assertEqual((stats.missing, stats.runs), (0, 1))

    def test_cfg_repeats_boot(self):
        _dev, items, _stats = self.run_log(8, reset_every=0, disconnect_every=0, gap_every=0)
        boots = [x for x in items if isinstance(x, BootLine)]
        cfgs = [x for x in items if isinstance(x, ConfigLine)]
        self.assertEqual(len(boots), 1)
        self.assertEqual(boots[0].settings["cfg_ms"], "%d" % (cfg.CFG_PERIOD_S * 1000))
        # ~27 s of device time (settle, 8 events and the next quiet stretch).
        self.assertGreaterEqual(len(cfgs), 4)
        self.assertTrue(all(c.as_boot().raw == boots[0].raw for c in cfgs))
        # The first one comes CFG_PERIOD_S after BOOT: after that many L lines.
        first = items.index(cfgs[0])
        self.assertEqual(len([x for x in items[:first] if isinstance(x, LogSample)]),
                         int(round(cfg.CFG_PERIOD_S * cfg.SIM_RATE_HZ)))

    def test_missing_count_equals_injected_gaps(self):
        # DRV-1: the count the status line shows equals the injected jumps.
        dev, items, stats = self.run_log(20, reset_every=7, disconnect_every=9, gap_every=3)
        self.assertGreater(len(dev.gaps), 3)
        self.assertEqual(stats.missing, sum(count for _n, count in dev.gaps))
        boots = len([x for x in items if isinstance(x, BootLine)])
        self.assertEqual(stats.runs, boots)            # Resets are new runs, never gaps
        self.assertGreater(len([x for x in items if isinstance(x, SimDisconnect)]), 0)

    def test_test_mode_snapshot_equals_l_lines(self):
        dev, items, _stats = self.run_log(4, reset_every=0, disconnect_every=0, gap_every=0,
                                          with_snapshots=True)
        by_n = {x.n: x for x in items if isinstance(x, LogSample)}
        snaps = [x for x in items if isinstance(x, Snapshot)]
        self.assertEqual(len(snaps), 4)
        for snap in snaps:
            cut = [by_n[n].model_text() for n in range(snap.trig_idx - 104, snap.trig_idx + 520)]
            self.assertEqual(cut, snap.rows)


if __name__ == "__main__":
    unittest.main()
