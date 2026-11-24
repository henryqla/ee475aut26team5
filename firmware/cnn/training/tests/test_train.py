"""
File:        test_train.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.1.0
Change:      0.1.0 (training-live-inference-handoff.md, request E): new file.
Description: Unit tests for train.py: choosing sessions (newest by press time,
             one session, missing ones), the whole-clip validation split, class
             weights, channel scaling, and the synthetic clips (readable, the
             right classes per session, byte-identical every run). With
             TensorFlow: the fixed model's size and ops, and short end-to-end
             runs on synthetic clips: model_data.h written; an export check
             failing (--fault bad-op, --fault mismatch) leaves the old file
             byte-identical; a class with no clips stops before training; no
             held-out NONE clips says false alarms per hour is missing.
"""

# ============================================================================
# Imports
# ============================================================================

import hashlib
import tempfile
import unittest
from pathlib import Path

import numpy as np

import helpers  # noqa: F401  (sets sys.path; must come first)
import cnn_config as cfg
import export as ex
import train as tr
import windows as wn
from clip_file import load_clip

try:
    import tensorflow  # noqa: F401
    HAVE_TF = True
except ImportError:
    HAVE_TF = False

# ============================================================================
# Functions
# ============================================================================


def sha(path: Path) -> str:
    """@return The file's SHA-256."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def small_opts(tmp: Path, **changes) -> tr.RunOptions:
    """@return RunOptions for a quick synthetic run inside tmp."""
    base = dict(synthetic=True, runs_dir=tmp / "runs", model_data_path=tmp / "model_data.h",
                max_epochs=2, synth_clips_per_class=3, synth_long_s=4.0, verbose=0)
    base.update(changes)
    return tr.RunOptions(**base)

# ============================================================================
# Classes
# ============================================================================


class TestSessions(unittest.TestCase):
    """Which sessions train and which are held out."""

    def test_newest_by_press_time_not_name(self):
        avail = ["2026-11-30_bench", "2026-12-02_car"]
        press = {"2026-11-30_bench": 2000, "2026-12-02_car": 1000}
        self.assertEqual((["2026-12-02_car"], ["2026-11-30_bench"]),
                         tr.choose_sessions(avail, None, None, press))

    def test_one_session_trains_on_everything(self):
        self.assertEqual((["a"], []), tr.choose_sessions(["a", "b"], ["a"], None, {}))

    def test_explicit_test_session_is_added(self):
        self.assertEqual((["a"], ["c"]), tr.choose_sessions(["a", "b", "c"], ["a"], ["c"], {}))

    def test_missing_session_stops(self):
        with self.assertRaises(tr.TrainStop) as cm:
            tr.choose_sessions(["a"], ["a", "zzz"], None, {})
        self.assertIn("zzz", str(cm.exception))

    def test_everything_held_out_stops(self):
        with self.assertRaises(tr.TrainStop):
            tr.choose_sessions(["a", "b"], ["a"], ["a"], {})

    def test_press_time_from_headers(self):
        with tempfile.TemporaryDirectory() as tmp:
            helpers.write_clip(Path(tmp) / "s1", "NONE-1.csv", helpers.records(900))
            self.assertEqual(1_790_000_000_000, tr.session_press_ms(Path(tmp), "s1"))
            self.assertEqual(-1, tr.session_press_ms(Path(tmp), "nothing"))


class TestSplits(unittest.TestCase):
    """Validation split, class weights, scaling."""

    def setUp(self):
        ids, y = [], []
        for k, c in enumerate(cfg.CLASSES):
            for j in range(20):
                ids += ["s/%s-%d.csv" % (c, j)] * 5
                y += [k] * 5
        self.ids, self.y = np.array(ids), np.array(y)

    def test_whole_clips_per_class_and_stable(self):
        val = tr.validation_clips(self.ids, self.y, 0.15, 475, 3)
        self.assertEqual(val, tr.validation_clips(self.ids, self.y, 0.15, 475, 3))
        for c in cfg.CLASSES:
            self.assertEqual(3, sum(1 for v in val if "/%s-" % c in v))   # round(0.15 x 20)
        mask = np.isin(self.ids, sorted(val))
        for cid in set(self.ids.tolist()):
            self.assertEqual(1, len(set(mask[self.ids == cid].tolist())))  # never split

    def test_small_classes(self):
        ids = np.array(["s/P-1", "s/B-1", "s/B-2", "s/N-1", "s/N-2", "s/N-3"])
        y = np.array([0, 1, 1, 2, 2, 2])
        val = tr.validation_clips(ids, y, 0.15, 475, 3)
        self.assertEqual(0, sum(1 for v in val if v.startswith("s/P")))
        self.assertEqual(1, sum(1 for v in val if v.startswith("s/B")))
        self.assertEqual(1, sum(1 for v in val if v.startswith("s/N")))

    def test_class_weights(self):
        w = tr.class_weights(np.array([0] * 10 + [1] * 10 + [2] * 20), 3)
        self.assertAlmostEqual(4 / 3, w[0])
        self.assertAlmostEqual(2 / 3, w[2])
        with self.assertRaises(ValueError):
            tr.class_weights(np.array([0, 2]), 3)

    def test_channel_stats(self):
        x = np.zeros((4, 10, 3), dtype=np.float32)
        x[:, :, 0] = np.arange(10)
        mean, std = tr.channel_stats(x)
        self.assertAlmostEqual(4.5, float(mean[0]), places=5)
        self.assertEqual(1.0, float(std[1]))                  # Flat channel


class TestSynthetic(unittest.TestCase):
    """Synthetic clips."""

    def test_readable_classes_and_press_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            sessions = tr.make_synthetic(Path(tmp), clips_per_class=2, long_s=5.0)
            self.assertEqual(list(cfg.SYNTH_SESSIONS), sessions)
            for s in sessions:
                labels = sorted(load_clip(p).label for p in (Path(tmp) / s).glob("*.csv"))
                if s == cfg.SYNTH_NO_NONE_SESSION:
                    self.assertNotIn("NONE", labels)
                else:
                    self.assertEqual(3, labels.count("NONE"))           # 2 short + 1 long
            clip = load_clip(Path(tmp) / "syn_1" / "POTHOLE-1.csv")
            self.assertEqual(2496, clip.rows)
            self.assertTrue(clip.ok.all())
            self.assertEqual(int(5.0 * 416), load_clip(Path(tmp) / "syn_1" / "NONE-3.csv").rows)
            press = [tr.session_press_ms(Path(tmp), s) for s in cfg.SYNTH_DEFAULT_SESSIONS]
            self.assertLess(press[0], press[1])                         # syn_2 is newest
            ws = wn.build_windows(Path(tmp), list(cfg.SYNTH_DEFAULT_SESSIONS),
                                  wn.WindowOptions(decimate=1))
            self.assertEqual(832, ws.X.shape[1])
            self.assertTrue(all(np.any(ws.y == k) for k in range(3)))

    def test_byte_identical(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            tr.make_synthetic(Path(a), clips_per_class=2, long_s=3.0)
            tr.make_synthetic(Path(b), clips_per_class=2, long_s=3.0)
            files = sorted(p.relative_to(a) for p in Path(a).rglob("*.csv"))
            self.assertTrue(files)
            for f in files:
                self.assertEqual(sha(Path(a) / f), sha(Path(b) / f))


@unittest.skipUnless(HAVE_TF, "needs TensorFlow")
class TestModel(unittest.TestCase):
    """The fixed model."""

    def test_size_and_ops(self):
        # Real scaling constants: with mean 0 and std 1 the converter drops the
        # multiply and the subtract as no-ops.
        model = tr.build_model(np.array([0.001, 0.2, -0.1], np.float32),
                               np.array([0.2, 2.7, 10.0], np.float32))
        self.assertEqual(6147, model.count_params())
        f = ex.tflite_facts(ex.to_float_tflite(model))
        self.assertEqual(set(cfg.TFLM_ALLOWED_OPS), set(f.ops))
        self.assertEqual((1, 832, 3), f.input_shape)
        self.assertEqual((1, 3), f.output_shape)

    def test_bad_op_hook(self):
        model = tr.build_model(np.zeros(3, np.float32), np.ones(3, np.float32), "bad-op")
        self.assertIn("TANH", ex.tflite_facts(ex.to_float_tflite(model)).ops)


@unittest.skipUnless(HAVE_TF, "needs TensorFlow")
class TestRun(unittest.TestCase):
    """Short end-to-end runs on synthetic clips (2 epochs)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.old = b"// an older model_data.h\n"
        (self.tmp / "model_data.h").write_bytes(self.old)

    def tearDown(self):
        self._tmp.cleanup()

    def test_written(self):
        r = tr.run_training(small_opts(self.tmp))
        self.assertEqual(tr.EXIT_WRITTEN, r.exit_code)
        self.assertTrue(r.model_version.startswith(cfg.SYNTH_PREFIX))
        text = (self.tmp / "model_data.h").read_text(encoding="utf-8")
        self.assertIn('#define MODEL_VERSION        "%s"' % r.model_version, text)
        self.assertEqual(text, (r.run_dir / "model_data.h").read_text(encoding="utf-8"))
        for name in ("report.txt", "windows_report.csv", "model.keras", "model.tflite",
                     "config.json"):
            self.assertTrue((r.run_dir / name).is_file(), name)
        report = r.report_path.read_text(encoding="utf-8")
        for part in ("Confusion matrix", "cutoff", "false alarms/h", "Result: all checks passed"):
            self.assertIn(part, report)

    def test_bad_op_leaves_old_file(self):
        r = tr.run_training(small_opts(self.tmp, fault="bad-op"))
        self.assertEqual(tr.EXIT_EXPORT_FAILED, r.exit_code)
        self.assertEqual(self.old, (self.tmp / "model_data.h").read_bytes())
        self.assertFalse((r.run_dir / "model_data.h").exists())
        self.assertIn("FAILED (allowed ops)", r.report_path.read_text(encoding="utf-8"))

    def test_mismatch_leaves_old_file(self):
        r = tr.run_training(small_opts(self.tmp, fault="mismatch"))
        self.assertEqual(tr.EXIT_EXPORT_FAILED, r.exit_code)
        self.assertEqual(self.old, (self.tmp / "model_data.h").read_bytes())
        self.assertIn("FAILED (TFLite matches Keras)", r.report_path.read_text(encoding="utf-8"))

    def test_empty_class_stops(self):
        r = tr.run_training(small_opts(self.tmp, sessions=[cfg.SYNTH_NO_NONE_SESSION]))
        self.assertEqual(tr.EXIT_STOPPED, r.exit_code)
        self.assertIn("no NONE windows", r.report_path.read_text(encoding="utf-8"))
        self.assertFalse((r.run_dir / "model.keras").exists())
        self.assertEqual(self.old, (self.tmp / "model_data.h").read_bytes())

    def test_no_held_out_none(self):
        r = tr.run_training(small_opts(self.tmp, sessions=["syn_1", cfg.SYNTH_NO_NONE_SESSION],
                                       test_sessions=[cfg.SYNTH_NO_NONE_SESSION]))
        report = r.report_path.read_text(encoding="utf-8")
        self.assertIn("No held-out NONE clips: false alarms per hour is missing.", report)
        self.assertIn("missing", report.split("false alarms/h")[-1])


if __name__ == "__main__":
    unittest.main()
