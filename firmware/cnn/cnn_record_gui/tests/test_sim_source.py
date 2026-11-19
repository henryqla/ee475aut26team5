"""
File:        test_sim_source.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
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
from record_core import RecordController
from sim_source import SimDevice, SimDisconnect
from snapshot_protocol import (BootLine, EventLine, HealthLine, Rejected, SampleLine,
                               Snapshot, SnapshotAssembler)
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


if __name__ == "__main__":
    unittest.main()
