"""
File:        test_sim_source.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.4.0
Change:      0.4.0 (training-live-inference-handoff.md, request G): BOOT has
             firmware-bench 0.1.5-bench's fields and SELFTEST follows it; PROB
             only after a full good window, every SIM_HOP samples; DET once per
             lockout; a gap restarts the window; PROB lines off and malformed
             lines (test controls); HLT inference counters.
             0.3.0 (manual-recording-overhaul-handoff.md, request B): rewritten
             for the one-build simulated device. The snapshot fault, S line
             and EVT tests are removed with those lines; the CFG and injected
             gap tests are kept in the new device's form. New: BOOT as
             firmware-bench 0.1.4-bench prints it, 416 L lines per second, the
             settle time, and each test control (event after a delay, gap,
             reset, ok=0 rows, disconnect).
Description: Unit tests for sim_source.py, parsed with the GUI's own parser,
             so the simulated device is checked against what the GUI reads.
             Run from cnn_record_gui/: python -m unittest discover -s tests
"""

# ============================================================================
# Imports
# ============================================================================

import unittest

import helpers  # Sets sys.path; must come before the GUI imports
import cnn_config as cfg
from sim_source import MALFORMED_LINES, SimDevice, SimDisconnect
from snapshot_protocol import (BootLine, ConfigLine, DetLine, HealthLine, LineParser, LogSample,
                               ProbLine, SelftestLine)

# ============================================================================
# Functions
# ============================================================================


def pull(stream, l_count):
    """
    Reads the stream until l_count more L lines have come.
    @param stream   SimDevice.stream() iterator.
    @param l_count  L lines to read.
    @return (items, device seconds): every parsed item (or SimDisconnect) in
            order, and the sum of the delays.
    """
    parser = LineParser()
    items, seconds, seen = [], 0.0, 0
    while seen < l_count:
        delay, item = next(stream)
        seconds += delay
        if isinstance(item, SimDisconnect):
            items.append(item)
            continue
        parsed = parser.feed(item)
        if parsed is not None:
            items.append(parsed)
            if isinstance(parsed, LogSample):
                seen += 1
    return items, seconds


def l_only(items):
    """@return The LogSamples in items."""
    return [x for x in items if isinstance(x, LogSample)]

# ============================================================================
# Tests
# ============================================================================


class TestSimDevice(unittest.TestCase):
    def setUp(self):
        self.dev = SimDevice()
        self.stream = self.dev.stream()

    def test_boot_like_the_firmware(self):
        items, _ = pull(self.stream, 1)
        self.assertIsInstance(items[0], BootLine)
        firmware = LineParser().feed(helpers.BOOT_INF).settings
        sim = items[0].settings
        self.assertEqual(list(sim), list(firmware))            # Same fields, same order
        model_keys = ("fw", "model", "arena_used", "selftest_diff")
        self.assertEqual({k: v for k, v in sim.items() if k not in model_keys},
                         {k: v for k, v in firmware.items() if k not in model_keys})
        self.assertEqual((sim["fw"], sim["model"]), (cfg.SIM_FW, cfg.SIM_MODEL))
        self.assertIsInstance(items[1], SelftestLine)          # Right after BOOT
        self.assertEqual(items[1].result, "pass")

    def test_prob_after_a_full_good_window(self):
        items, _ = pull(self.stream, 6 * 416)
        probs = [x for x in items if isinstance(x, ProbLine)]
        settle = int(round(cfg.SIM_SETTLE_S * 416))
        self.assertGreaterEqual(probs[0].n, settle + cfg.SIM_WINDOW)   # No PROB before it
        self.assertEqual({b.n - a.n for a, b in zip(probs, probs[1:])}, {cfg.SIM_HOP})
        self.assertTrue(all(abs(sum(p.probs) - 1.0) < 2e-4 for p in probs))
        self.assertTrue(all(p.probs[2] == max(p.probs) for p in probs))   # Quiet: NONE
        last_hlt = max(i for i, x in enumerate(items) if isinstance(x, HealthLine))
        before = sum(isinstance(x, ProbLine) for x in items[:last_hlt])
        self.assertEqual(items[last_hlt].fields["inferences"], before)   # Counted as sent
        self.assertEqual(items[last_hlt].fields["inf_skips"], 0)

    def test_det_at_most_one_per_lockout(self):
        pull(self.stream, 5 * 416)
        self.dev.inject_event("SPEED_BUMP", 0.5)
        self.dev.inject_event("SPEED_BUMP", 1.0)               # Two bumps 0.5 s apart
        items, _ = pull(self.stream, 5 * 416)
        dets = [x for x in items if isinstance(x, DetLine)]
        lockout = int(round(cfg.SIM_DET_LOCKOUT_MS * 416 / 1000))
        self.assertTrue(dets)
        self.assertEqual(dets[0].label, "SPEED_BUMP")
        self.assertGreaterEqual(dets[0].conf, cfg.SIM_DET_CONF)
        self.assertTrue(all(b.n - a.n >= lockout for a, b in zip(dets, dets[1:])))

    def test_gap_restarts_the_window(self):
        pull(self.stream, 5 * 416)
        self.dev.inject_gap(cfg.SIM_GAP_SAMPLES)
        items, _ = pull(self.stream, 3 * 416)
        lost_at = self.dev.gaps[0][0]
        probs = [x.n for x in items if isinstance(x, ProbLine)]
        self.assertFalse([n for n in probs if lost_at <= n < lost_at + cfg.SIM_WINDOW])

    def test_prob_off_and_malformed(self):
        items, _ = pull(self.stream, 5 * 416)
        last_n = l_only(items)[-1].n      # Test controls act from the next sample on
        self.dev.set_prob(False)
        self.dev.inject_malformed()
        parser = LineParser()
        seen = []
        stream_items = 0
        while stream_items < 3 * 416:
            _delay, item = next(self.stream)
            out = parser.feed(item)
            if out is not None:
                seen.append(out)
            stream_items += 1
        self.assertFalse([x for x in seen if isinstance(x, ProbLine) and x.n > last_n])  # Off
        self.assertTrue([x for x in seen if isinstance(x, LogSample)])   # L lines go on
        self.assertEqual(sum(parser.malformed.values()), len(MALFORMED_LINES))

    def test_rate_and_settle(self):
        items, seconds = pull(self.stream, 4 * 416)
        samples = l_only(items)
        self.assertAlmostEqual(seconds, 4.0, places=2)         # 416 L lines per device second
        self.assertEqual([s.n for s in samples], list(range(1, 4 * 416 + 1)))
        settle = int(round(cfg.SIM_SETTLE_S * 416))
        self.assertTrue(all(not s.ok for s in samples[:settle]))
        self.assertTrue(all(s.ok for s in samples[settle:]))
        self.assertEqual(sum(isinstance(x, HealthLine) for x in items), 3)  # At 1, 2 and 3 s

    def test_cfg_repeats_boot(self):
        items, _ = pull(self.stream, int(cfg.CFG_PERIOD_S * 416) + 1)
        boot = items[0]
        cfgs = [x for x in items if isinstance(x, ConfigLine)]
        self.assertEqual(len(cfgs), 1)
        self.assertEqual(cfgs[0].as_boot().raw, boot.raw)

    def test_event_after_the_delay(self):
        pull(self.stream, 1000)
        self.dev.inject_event("POTHOLE", 1.5)
        samples = l_only(pull(self.stream, 2000)[0])
        first = self.dev.events[0][0]
        self.assertEqual(first, 1001 + int(round(1.5 * 416)))
        # Pitch rate: noise of ~1.5 dps before, a ~60 dps nose-down lobe in it
        # (negative with the bench mapping).
        before = max(abs(s.model[2]) for s in samples if s.n < first)
        during = min(s.model[2] for s in samples if first <= s.n < first + 100)
        self.assertLess(before, 15.0)                          # Only noise before it
        self.assertLess(during, -30.0)
        self.dev.inject_event("NONE", 0.5)                     # Nothing happens
        pull(self.stream, 1)
        self.assertEqual(len(self.dev.events), 1)

    def test_gap(self):
        pull(self.stream, 900)
        self.dev.inject_gap(cfg.SIM_GAP_SAMPLES)
        samples = l_only(pull(self.stream, 100)[0])
        self.assertEqual(samples[0].n, 901 + cfg.SIM_GAP_SAMPLES)   # n jumps
        self.assertEqual(self.dev.gaps, [(901, cfg.SIM_GAP_SAMPLES)])

    def test_ok0(self):
        pull(self.stream, 1000)
        self.dev.inject_ok0(cfg.SIM_OK0_SAMPLES)
        samples = l_only(pull(self.stream, 200)[0])
        self.assertEqual(sum(not s.ok for s in samples), cfg.SIM_OK0_SAMPLES)
        self.assertFalse(samples[0].ok)

    def test_reset(self):
        pull(self.stream, 500)
        self.dev.inject_reset()
        items, _ = pull(self.stream, 10)
        self.assertIsInstance(items[0], BootLine)
        self.assertEqual([s.n for s in l_only(items)], list(range(1, 11)))   # n restarts
        self.assertEqual(self.dev.boots, 2)

    def test_disconnect(self):
        pull(self.stream, 500)
        self.dev.inject_disconnect(2.0)
        items, _ = pull(self.stream, 5)
        self.assertIsInstance(items[0], SimDisconnect)
        self.assertEqual(items[0].duration_s, 2.0)
        self.assertIsInstance(items[1], BootLine)               # The board rebooted
        self.assertEqual(l_only(items)[0].n, 1)


if __name__ == "__main__":
    unittest.main()
