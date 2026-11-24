"""
File:        test_pipeline_tf.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: End-to-end tests of train.py with TensorFlow, on the trn1 and
             trn3 sample sets (3 epochs, so it runs in under a minute): two
             runs with the same seed give identical metrics and fingerprints,
             every export file is written, model_data.h holds the exact
             .tflite bytes, the frozen test set has the right shapes, TFLite
             agrees with Keras, and a mixed set stops the run with the reason
             in train.log. Skipped when TensorFlow is not installed.
"""

# ============================================================================
# Imports
# ============================================================================

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np

import helpers  # sets sys.path; must come first
from make_sample_set import build_sets

# ============================================================================
# Tests
# ============================================================================


@unittest.skipUnless(helpers.tensorflow_available(), "TensorFlow is not installed")
class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.sets = build_sets(cls.root / "data", ("trn1", "trn3"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def options(self, name):
        from train import RunOptions
        return RunOptions(data_dir=self.sets[name], allow_sim=True,
                          out_root=self.root / "runs", max_epochs=3)

    def test_two_runs_identical_and_export(self):
        from train import run_training
        a = run_training(self.options("trn1"))
        b = run_training(self.options("trn1"))
        ja = json.loads((a / "report.json").read_text(encoding="utf-8"))
        jb = json.loads((b / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(ja["metrics"], jb["metrics"])
        self.assertEqual(ja["about"]["fingerprint"], jb["about"]["fingerprint"])
        self.assertTrue(ja["metrics"]["reportable"])          # 40 takes per class

        for name in ("model.keras", "model.tflite", "model_data.h", "frozen_test_set.npz",
                     "report.md", "report.json", "train.log"):
            self.assertTrue((a / name).is_file(), name)
        tflite = (a / "model.tflite").read_bytes()
        header = (a / "model_data.h").read_text(encoding="utf-8")
        self.assertRegex(header, r"#define CNN_MODEL_LEN\s+%d\b" % len(tflite))
        self.assertIn("CONV_2D", header)

        n_test = sum(ja["metrics"]["counts"]["test"].values())
        with np.load(a / "frozen_test_set.npz") as f:
            self.assertEqual(f["x"].shape, (n_test, 624, 3))
            self.assertEqual(f["expected_probs"].shape, (n_test, 3))
            np.testing.assert_allclose(f["expected_probs"].sum(axis=1), 1.0, atol=1e-4)
        self.assertLess(ja["metrics"]["export"]["max_abs_diff"], 1e-4)

    def test_mixed_set_stops_with_reason(self):
        from train import TrainingError, run_training
        with self.assertRaises(TrainingError) as ctx:
            run_training(self.options("trn3"))
        log = (ctx.exception.run_dir / "train.log").read_text(encoding="utf-8")
        self.assertIn("Refusing a mixed set", log)
        self.assertIn("wrong length", log)
        self.assertFalse((ctx.exception.run_dir / "model.tflite").exists())


if __name__ == "__main__":
    unittest.main()
