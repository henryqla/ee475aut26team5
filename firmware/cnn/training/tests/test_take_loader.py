"""
File:        test_take_loader.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for take_loader.py (handoff, "Loader checks"):
             accepted takes and their values, simulated takes refused unless
             allowed, UNLABELED counted, empty / unparseable files skipped and
             listed, wrong length and bad labels rejected, mixed header or
             BOOT settings refused with the files named, _deleted ignored, the
             minimum-takes check, and the TRN-3 / TRN-4 sample sets. Writes
             real take files (make_sample_set.py, TakeStore) into a temporary
             folder. Needs numpy.
"""

# ============================================================================
# Imports
# ============================================================================

import tempfile
import unittest
from pathlib import Path

import numpy as np

import helpers  # noqa: F401  (sets sys.path; must come first)
import cnn_config as cfg
from make_sample_set import SampleWriter, build_sets, cut_rows
from take_loader import (MixedSettingsError, list_sessions, load_dataset, parse_boot,
                         session_type, thin_classes)

# ============================================================================
# Tests
# ============================================================================


class TestLoader(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.w = SampleWriter(self.root, np.random.default_rng(1))

    def tearDown(self):
        self.tmp.cleanup()

    def load(self, **kwargs):
        kwargs.setdefault("allow_sim", True)
        return load_dataset(self.root, **kwargs)

    def test_accepts_good_takes(self):
        self.w.session("s1_bench", {"POTHOLE": 2, "SPEED_BUMP": 1})
        self.w.session("s2_car_route1", {"FALSE_TRIGGER": 2})
        r = self.load()
        self.assertEqual(len(r.takes), 5)
        self.assertEqual({t.x.shape for t in r.takes}, {(624, 3)})
        self.assertEqual({t.x.dtype for t in r.takes}, {np.dtype(np.float32)})
        self.assertEqual(sorted({t.session_type for t in r.takes}), ["bench", "car"])
        self.assertEqual((r.skipped, r.rejected, r.unlabeled), ([], [], []))
        self.assertEqual(r.settings, {"rate_hz": "416", "n_pre": "104", "n_post": "520",
                                      "n_rows": "624", "channels": "az_hp/gx/gy",
                                      "fw": cfg.SIM_FW})
        self.assertEqual(r.boot_settings["hpf_hz"], "0.50")
        self.assertGreater(r.n_with_boot, 0)
        self.assertLess(r.n_with_boot, 5)   # Every 2nd take keeps its BOOT line

    def test_values_match_file(self):
        path = self.w.take("s1_bench", "POTHOLE")
        r = self.load()
        with open(path, encoding="utf-8") as fh:
            rows = [line for line in fh if not line.startswith("#")]
        self.assertEqual(len(rows), 624)
        for i in (0, 104, 623):
            np.testing.assert_allclose(r.takes[0].x[i], [float(v) for v in rows[i].split(",")],
                                       rtol=0, atol=1e-6)

    def test_simulated_refused_unless_allowed(self):
        self.w.take("s1_bench", "POTHOLE")
        r = self.load(allow_sim=False)
        self.assertEqual(r.takes, [])
        self.assertEqual(len(r.rejected), 1)
        self.assertIn("simulated", r.rejected[0][1])

    def test_unlabeled_skipped_and_counted(self):
        self.w.take("s1_bench", "POTHOLE")
        self.w.take("s1_bench", cfg.UNLABELED, true_label="SPEED_BUMP")
        r = self.load()
        self.assertEqual(len(r.takes), 1)
        self.assertEqual([p.name for p in r.unlabeled], ["UNLABELED-1.csv"])

    def test_empty_unparseable_and_odd_files_skipped(self):
        self.w.take("s1_bench", "POTHOLE")
        folder = self.root / "s1_bench"
        (folder / "POTHOLE-50.csv").write_bytes(b"")
        (folder / "POTHOLE-51.csv").write_text("# label=POTHOLE\n1,2,x\n", encoding="utf-8")
        (folder / "POTHOLE-52.csv").write_text("# label=POTHOLE\n", encoding="utf-8")
        (folder / "notes.csv").write_text("hello\n", encoding="utf-8")
        (folder / "readme.txt").write_text("not a take\n", encoding="utf-8")
        r = self.load()
        reasons = {p.name: why for p, why in r.skipped}
        self.assertEqual(set(reasons), {"POTHOLE-50.csv", "POTHOLE-51.csv", "POTHOLE-52.csv",
                                        "notes.csv"})
        self.assertIn("empty", reasons["POTHOLE-50.csv"])
        self.assertIn("unparseable", reasons["POTHOLE-51.csv"])
        self.assertIn("no data rows", reasons["POTHOLE-52.csv"])
        self.assertIn("not a take file name", reasons["notes.csv"])
        self.assertEqual(len(r.takes), 1)

    def test_wrong_length_rejected(self):
        good = self.w.take("s1_bench", "POTHOLE")
        bad = self.w.take("s1_bench", "SPEED_BUMP")
        cut_rows(bad, 24)
        r = self.load()
        self.assertEqual([t.path.name for t in r.takes], [good.name])
        self.assertEqual([p.name for p, _ in r.rejected], [bad.name])
        self.assertIn("wrong length: 600 rows, header says 624", r.rejected[0][1])

    def test_label_mismatch_and_unknown_label_rejected(self):
        path = self.w.take("s1_bench", "POTHOLE")
        text = path.read_text(encoding="utf-8")
        path.with_name("SPEED_BUMP-77.csv").write_text(text, encoding="utf-8", newline="")
        path.with_name("BUMPY-1.csv").write_text(
            text.replace("# label=POTHOLE", "# label=BUMPY"), encoding="utf-8", newline="")
        r = self.load()
        reasons = {p.name: why for p, why in r.rejected}
        self.assertIn("differ", reasons["SPEED_BUMP-77.csv"])
        self.assertIn("not one of", reasons["BUMPY-1.csv"])
        self.assertEqual([t.path.name for t in r.takes], [path.name])

    def test_mixed_settings_refused_with_files_named(self):
        for _ in range(3):
            self.w.take("s1_bench", "POTHOLE")
        odd = self.w.take("s2_bench", "POTHOLE", n_pre=208, n_post=416)
        bad = self.w.take("s2_bench", "SPEED_BUMP")
        cut_rows(bad, 24)
        with self.assertRaises(MixedSettingsError) as ctx:
            self.load()
        msg = str(ctx.exception)
        self.assertIn("s2_bench/%s" % odd.name, msg)
        self.assertIn("n_pre=208", msg)
        self.assertIn("header settings", msg)
        # The bad take is rejected on its own, not part of the refusal.
        self.assertEqual([p.name for p, _ in ctx.exception.report.rejected], [bad.name])

    def test_boot_settings_mismatch_refused(self):
        self.w.take("s1_bench", "POTHOLE", boot=True)
        odd = self.w.take("s1_bench", "SPEED_BUMP", boot=True, hpf="1.00")
        self.w.take("s1_bench", "FALSE_TRIGGER", boot=False)   # No BOOT: not compared
        with self.assertRaises(MixedSettingsError) as ctx:
            self.load()
        msg = str(ctx.exception)
        self.assertIn("BOOT settings", msg)
        self.assertIn("hpf_hz=1.00", msg)
        self.assertIn("s1_bench/%s" % odd.name, msg)

    def test_deleted_folder_ignored(self):
        p = self.w.take("s1_bench", "POTHOLE")
        self.w.store.delete(p)
        self.w.take("s1_bench", "SPEED_BUMP")
        r = self.load()
        self.assertEqual([t.label for t in r.takes], ["SPEED_BUMP"])
        self.assertEqual(list_sessions(self.root), ["s1_bench"])

    def test_named_sessions(self):
        self.w.take("s1_bench", "POTHOLE")
        self.w.take("s2_bench", "POTHOLE")
        r = self.load(sessions=["s2_bench"])
        self.assertEqual({t.session for t in r.takes}, {"s2_bench"})
        with self.assertRaises(ValueError):
            self.load(sessions=["nope"])

    def test_missing_data_folder(self):
        with self.assertRaises(FileNotFoundError):
            load_dataset(self.root / "nope")

    def test_thin_classes(self):
        self.w.session("s1_bench", {"POTHOLE": 2, "SPEED_BUMP": 1})
        self.w.session("s2_car", {"POTHOLE": 2, "SPEED_BUMP": 2, "FALSE_TRIGGER": 2})
        r = self.load()
        self.assertEqual(thin_classes(r.takes, minimum=2),
                         [("bench", "SPEED_BUMP", 1), ("bench", "FALSE_TRIGGER", 0)])

    def test_session_type_and_parse_boot(self):
        self.assertEqual(session_type("2026-11-14_car_route1"), "car")
        self.assertEqual(session_type("2026-11-12_bench"), "bench")
        self.assertEqual(session_type("carpool_x"), "other")
        self.assertEqual(parse_boot("BOOT,fw=x,hpf_hz=0.50"), {"fw": "x", "hpf_hz": "0.50"})
        self.assertEqual(parse_boot("missing (not seen on this connection)"), {})


class TestSampleSets(unittest.TestCase):
    def test_trn3_refused_and_trn4_skips_and_thin(self):
        with tempfile.TemporaryDirectory() as tmp:
            sets = build_sets(Path(tmp), ("trn3", "trn4"))
            with self.assertRaises(MixedSettingsError) as ctx:
                load_dataset(sets["trn3"], allow_sim=True)
            self.assertEqual(str(ctx.exception).count("mixC_bench/POTHOLE-"), 3)
            self.assertEqual(len(ctx.exception.report.rejected), 1)

            r = load_dataset(sets["trn4"], allow_sim=True)
            self.assertEqual(len(r.unlabeled), 1)
            self.assertEqual(sorted(p.name for p, _ in r.skipped),
                             ["POTHOLE-900.csv", "SPEED_BUMP-900.csv"])
            self.assertEqual(thin_classes(r.takes), [("bench", "FALSE_TRIGGER", 5)])

    def test_rebuild_identical_and_foreign_files_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = build_sets(out, ("trn3",))["trn3"]
            snap = {p.relative_to(first): p.read_bytes() for p in first.rglob("*.csv")}
            build_sets(out, ("trn3",))
            again = {p.relative_to(first): p.read_bytes() for p in first.rglob("*.csv")}
            self.assertEqual(snap, again)
            (first / "mixA_bench" / "POTHOLE-99.csv").write_text("# label=POTHOLE\n1,2,3\n",
                                                                  encoding="utf-8")
            with self.assertRaises(RuntimeError):
                build_sets(out, ("trn3",))
            self.assertTrue((first / "mixA_bench" / "POTHOLE-99.csv").exists())


if __name__ == "__main__":
    unittest.main()
