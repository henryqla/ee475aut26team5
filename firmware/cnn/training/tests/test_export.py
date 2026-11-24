"""
File:        test_export.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.1.0
Change:      0.1.0 (training-live-inference-handoff.md, request E): new file.
Description: Unit tests for export.py: each export check passes and fails
             when it should (shapes, an op outside the allowed list, the size
             limit, a Keras/TFLite mismatch), model_data.h holds what the
             handoff lists (and no cutoff), its numbers read back exactly, and
             the atomic write. The flatbuffer tests need TensorFlow and are
             skipped without it.
"""

# ============================================================================
# Imports
# ============================================================================

import re
import tempfile
import unittest
from pathlib import Path

import numpy as np

import helpers  # noqa: F401  (sets sys.path; must come first)
import cnn_config as cfg
import export as ex

try:
    import tensorflow  # noqa: F401
    HAVE_TF = True
except ImportError:
    HAVE_TF = False

# ============================================================================
# Functions
# ============================================================================


def facts(**changes) -> ex.TfliteFacts:
    """@return Facts of a good flatbuffer, with changes applied."""
    base = dict(size=31000, input_shape=(1, 832, 3), input_dtype="float32",
                output_shape=(1, 3), output_dtype="float32", ops=list(cfg.TFLM_ALLOWED_OPS),
                activation_bytes=26000)
    base.update(changes)
    return ex.TfliteFacts(**base)


def checks(f=None, keras=None, tflite=None) -> dict:
    """@return {check name: Check} for the given facts and outputs."""
    keras = np.array([[0.8, 0.1, 0.1], [0.2, 0.7, 0.1]]) if keras is None else keras
    tflite = keras.copy() if tflite is None else tflite
    out = ex.export_checks(f or facts(), keras, tflite, 832, 3, 3, cfg.TFLM_ALLOWED_OPS,
                           cfg.MAX_TFLITE_BYTES, cfg.TFLITE_MATCH_TOL)
    return {c.name: c for c in out}


def header_info() -> ex.HeaderInfo:
    """@return HeaderInfo for a synthetic model."""
    return ex.HeaderInfo(model_version="synthetic-20261124-120000",
                         generated="2026-11-24T12:00:00-08:00", commit="abc1234",
                         run_lines=["Training session syn_1: POTHOLE 3 clips"],
                         class_names=cfg.CLASSES, channels=cfg.MODEL_CHANNELS, window=832,
                         hop=208, rate_hz=416, ops=sorted(cfg.TFLM_ALLOWED_OPS))


def array_body(text: str, name: str) -> str:
    """@return The text between '<name>... = {' and its closing '};'."""
    start = text.index(name)
    start = text.index("= {", start) + 3
    return text[start:text.index("\n};", start)]

# ============================================================================
# Classes
# ============================================================================


class TestChecks(unittest.TestCase):
    """Each export check."""

    def test_all_pass(self):
        self.assertTrue(all(c.ok for c in checks().values()))
        self.assertTrue(ex.checks_passed(list(checks().values())))

    def test_op_outside_the_list(self):
        c = checks(facts(ops=list(cfg.TFLM_ALLOWED_OPS) + ["TANH"]))[ex.CHECK_OPS]
        self.assertFalse(c.ok)
        self.assertIn("TANH", c.detail)

    def test_size_limit_is_under(self):
        self.assertFalse(checks(facts(size=cfg.MAX_TFLITE_BYTES))[ex.CHECK_SIZE].ok)
        self.assertTrue(checks(facts(size=cfg.MAX_TFLITE_BYTES - 1))[ex.CHECK_SIZE].ok)

    def test_shapes(self):
        self.assertFalse(checks(facts(input_shape=(1, 208, 3)))[ex.CHECK_INPUT].ok)
        self.assertFalse(checks(facts(input_dtype="int8"))[ex.CHECK_INPUT].ok)
        self.assertFalse(checks(facts(output_shape=(1, 4)))[ex.CHECK_OUTPUT].ok)

    def test_mismatch(self):
        k = np.array([[0.8, 0.1, 0.1], [0.2, 0.7, 0.1]])
        self.assertFalse(checks(keras=k, tflite=k + 1e-3)[ex.CHECK_MATCH].ok)
        self.assertTrue(checks(keras=k, tflite=k + 5e-5)[ex.CHECK_MATCH].ok)
        swapped = np.array([[0.8, 0.1, 0.1], [0.7, 0.2, 0.1]])
        self.assertFalse(checks(keras=k, tflite=swapped)[ex.CHECK_MATCH].ok)

    def test_no_windows_fails(self):
        empty = np.zeros((0, 3))
        self.assertFalse(checks(keras=empty, tflite=empty)[ex.CHECK_MATCH].ok)


class TestHeader(unittest.TestCase):
    """model_data.h's contents."""

    @classmethod
    def setUpClass(cls):
        rng = np.random.default_rng(1)
        cls.model = bytes(range(256)) * 3 + b"\x01\x02"
        cls.test_x = rng.normal(0, 3, (3, 832, 3)).astype(np.float32)
        cls.test_p = np.array([[0.9, 0.05, 0.05], [0.1, 0.8, 0.1], [1e-7, 0.0, 1.0]],
                              dtype=np.float32)
        cls.text = ex.model_data_header(header_info(), cls.model, cls.test_x, cls.test_p,
                                        ["a", "b", "c"])

    def test_macros_and_names(self):
        t = self.text
        for line in ('#define MODEL_VERSION        "synthetic-20261124-120000"',
                     "#define MODEL_WINDOW         832", "#define MODEL_HOP            208",
                     "#define MODEL_RATE_HZ        416", "#define MODEL_TEST_N         3",
                     '#define MODEL_CHANNELS       "z_hp,roll,pitch"',
                     "#define MODEL_CLASS_COUNT    3"):
            self.assertIn(line, t)
        names = re.findall(r'^  "(\w+)",$', array_body(t, "g_model_class_names"), re.M)
        self.assertEqual(list(cfg.CLASSES), names)
        self.assertIn("#ifndef MODEL_DATA_H", t)
        self.assertIn("commit abc1234", t)

    def test_no_cutoff(self):
        self.assertNotIn("#define DET_CONF", self.text)
        self.assertNotRegex(self.text, r"DET_CONF\s+[0-9]")

    def test_model_bytes_aligned_and_exact(self):
        self.assertIn("alignas(16) static const unsigned char g_model_data[] = {", self.text)
        body = array_body(self.text, "g_model_data[]")
        got = bytes(int(h, 16) for h in re.findall(r"0x([0-9a-f]{2})", body))
        self.assertEqual(self.model, got)
        self.assertIn("g_model_data_len = %d;" % len(self.model), self.text)
        self.assertTrue(all(len(re.findall("0x", s)) <= ex.BYTES_PER_LINE
                            for s in body.splitlines()))

    def test_self_test_values_read_back(self):
        nums = re.findall(r"(-?[0-9.]+(?:e[-+]?[0-9]+)?)f",
                          array_body(self.text, "g_model_test_x"))
        got = np.array([np.float32(v) for v in nums], dtype=np.float32)
        self.assertTrue(np.array_equal(self.test_x.reshape(-1), got))
        nums = re.findall(r"(-?[0-9.]+(?:e[-+]?[0-9]+)?)f",
                          array_body(self.text, "g_model_test_p"))
        self.assertTrue(np.array_equal(self.test_p.reshape(-1),
                                       np.array([np.float32(v) for v in nums])))

    def test_float_literals(self):
        self.assertEqual("0.0f", ex._cfloat(0.0))
        self.assertEqual("2.0f", ex._cfloat(2.0))
        self.assertTrue(ex._cfloat(1e-7).endswith("e-07f"))
        with self.assertRaises(ValueError):
            ex._cfloat(float("inf"))


class TestWriteAtomic(unittest.TestCase):
    """The swap-in write."""

    def test_replaces_and_leaves_no_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "model_data.h"
            path.write_text("old", encoding="utf-8")
            ex.write_atomic(path, "new\nline\n")
            self.assertEqual(b"new\nline\n", path.read_bytes())
            self.assertEqual(["model_data.h"], sorted(p.name for p in Path(tmp).iterdir()))


@unittest.skipUnless(HAVE_TF, "needs TensorFlow")
class TestFlatbuffer(unittest.TestCase):
    """Facts read from a real flatbuffer."""

    def test_ops_without_delegate(self):
        import tensorflow as tf
        tf.keras.utils.set_random_seed(1)
        inp = tf.keras.layers.Input((832, 3))
        x = tf.keras.layers.AveragePooling1D(4)(inp)
        x = tf.keras.layers.GlobalAveragePooling1D()(x)
        model = tf.keras.Model(inp, tf.keras.layers.Dense(3, activation="softmax")(x))
        fb = ex.to_float_tflite(model)
        f = ex.tflite_facts(fb)
        self.assertNotIn("DELEGATE", f.ops)
        self.assertIn("AVERAGE_POOL_2D", f.ops)
        self.assertEqual((1, 832, 3), f.input_shape)
        x = np.random.default_rng(2).normal(size=(4, 832, 3)).astype(np.float32)
        self.assertLess(np.max(np.abs(ex.run_tflite(fb, x) - model.predict(x, verbose=0))),
                        1e-5)


if __name__ == "__main__":
    unittest.main()
