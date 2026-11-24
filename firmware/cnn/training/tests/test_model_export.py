"""
File:        test_model_export.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for the parts of model_export.py that need no
             TensorFlow: the generated model_data.h (bytes round-trip,
             16-byte alignment, length, class order, input contract, BOOT
             macros only when known, op list, warnings, text from take files
             escaped) and the frozen test set file. Needs numpy.
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
from model_export import ModelInfo, model_data_header, write_frozen_test_set

# ============================================================================
# Functions
# ============================================================================


def make_info(**changes) -> ModelInfo:
    """
    @param changes  Fields to change from the defaults.
    @return A ModelInfo like a real run's.
    """
    info = dict(
        model_version="2026-11-22_153000", generated="2026-11-22T15:30:00-08:00",
        script="firmware/cnn/training/train.py 0.1.0", commit="abc123",
        class_names=("POTHOLE", "SPEED_BUMP", "FALSE_TRIGGER"),
        settings={"rate_hz": "416", "n_pre": "104", "n_post": "520", "n_rows": "624",
                  "channels": "az_hp/gx/gy", "fw": "0.1.2-bench"},
        boot_settings={"odr": "416", "acc_fs": "16", "gyr_fs": "500", "hpf_hz": "0.50",
                       "settle_ms": "2000"},
        n_with_boot=10, n_takes=40,
        sessions={"train": [("a_bench", 20)], "val": [("b_bench", 10)],
                  "test": [("c_bench", 10)]},
        class_counts={"train": {"POTHOLE": 8, "SPEED_BUMP": 7, "FALSE_TRIGGER": 5},
                      "val": {"POTHOLE": 4, "SPEED_BUMP": 3, "FALSE_TRIGGER": 3},
                      "test": {"POTHOLE": 4, "SPEED_BUMP": 3, "FALSE_TRIGGER": 3}},
        n_train_oversampled=24, norm_mean=[0.001, -0.5, 0.25], norm_std=[0.3, 40.0, 60.0],
        ops=["CONV_2D", "SOFTMAX"], reportable=True)
    info.update(changes)
    return ModelInfo(**info)


def array_bytes(text: str) -> bytes:
    """
    @param text  model_data.h text.
    @return The bytes in its cnn_model_data array.
    """
    body = text.split("cnn_model_data[CNN_MODEL_LEN] = {", 1)[1].split("};", 1)[0]
    return bytes(int(h, 16) for h in re.findall(r"0x([0-9a-f]{2})", body))

# ============================================================================
# Tests
# ============================================================================


class TestHeader(unittest.TestCase):
    def setUp(self):
        self.model = bytes(range(256)) * 3 + b"\x01\x02"   # 770 bytes

    def test_bytes_alignment_length(self):
        text = model_data_header(self.model, make_info())
        self.assertEqual(array_bytes(text), self.model)
        self.assertIn("alignas(16) static const unsigned char cnn_model_data[CNN_MODEL_LEN]", text)
        self.assertRegex(text, r"#define CNN_MODEL_LEN\s+770\b")
        self.assertTrue(text.startswith("/*\n"))
        self.assertIn("#ifndef CNN_MODEL_DATA_H\n#define CNN_MODEL_DATA_H\n", text)
        self.assertTrue(text.endswith("#endif  // CNN_MODEL_DATA_H\n"))
        self.assertNotIn("\r", text)

    def test_contract_classes_ops(self):
        text = model_data_header(self.model, make_info())
        for name, value in (("CNN_N_ROWS", "624"), ("CNN_N_PRE", "104"), ("CNN_N_POST", "520"),
                            ("CNN_RATE_HZ", "416"), ("CNN_N_CH", "3"), ("CNN_N_CLASSES", "3"),
                            ("CNN_N_OPS", "2"), ("CNN_HPF_CUTOFF_HZ", "0.50f"),
                            ("CNN_SETTLE_MS", "2000"), ("CNN_GYRO_FS_DPS", "500"),
                            ("CNN_ACCEL_FS_G", "16"), ("CNN_IMU_ODR_HZ", "416")):
            self.assertRegex(text, r"#define %s\s+%s(\s|$)" % (name, re.escape(value)), name)
        for name, value in (("CNN_CHANNELS", "az_hp/gx/gy"), ("CNN_UNITS", "g/dps/dps"),
                            ("CNN_FW", "0.1.2-bench"), ("CNN_OPS", "CONV_2D,SOFTMAX"),
                            ("CNN_MODEL_VERSION", "2026-11-22_153000")):
            self.assertRegex(text, r'#define %s\s+"%s"' % (name, re.escape(value)), name)
        self.assertIn('= {"POTHOLE", "SPEED_BUMP", "FALSE_TRIGGER"};', text)
        self.assertIn("inside the model graph", text)
        self.assertIn("a_bench (20)", text)
        self.assertNotIn("NOT REPORTABLE", text)

    def test_boot_unknown_not_defined(self):
        text = model_data_header(self.model, make_info(boot_settings={}, n_with_boot=0))
        self.assertNotRegex(text, r"#define CNN_HPF_CUTOFF_HZ")
        self.assertIn("CNN_HPF_CUTOFF_HZ", text)            # Named in the comment instead

    def test_not_reportable_warning(self):
        text = model_data_header(self.model, make_info(reportable=False))
        self.assertIn("NOT REPORTABLE", text)

    def test_text_from_takes_cannot_break_the_file(self):
        info = make_info(commit="abc */ int x; /*", settings=dict(
            make_info().settings, fw='0.1"\\x\n'))
        text = model_data_header(self.model, info)
        self.assertEqual(text.count("*/"), 1)               # Only the header block's end
        self.assertRegex(text, r'#define CNN_FW\s+"0\.1\\"\\\\x\?"')


class TestFrozenSet(unittest.TestCase):
    def test_round_trip(self):
        x = np.random.default_rng(0).normal(size=(2, 624, 3)).astype(np.float32)
        probs = np.array([[0.7, 0.2, 0.1], [0.1, 0.1, 0.8]], dtype=np.float32)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frozen_test_set.npz"
            write_frozen_test_set(path, x, np.array([0, 2]), probs, probs + 1e-7,
                                  ["c_bench/POTHOLE-1.csv", "c_bench/FALSE_TRIGGER-1.csv"],
                                  ["POTHOLE", "UNKNOWN"],
                                  ("POTHOLE", "SPEED_BUMP", "FALSE_TRIGGER"), "run1")
            with np.load(path) as f:
                np.testing.assert_array_equal(f["x"], x)
                np.testing.assert_array_equal(f["expected_probs"], probs)
                self.assertEqual(f["y_true"].tolist(), [0, 2])
                self.assertEqual(f["files"].tolist()[1], "c_bench/FALSE_TRIGGER-1.csv")
                self.assertEqual(f["class_names"].tolist(), ["POTHOLE", "SPEED_BUMP",
                                                             "FALSE_TRIGGER"])
                self.assertEqual(str(f["model_version"]), "run1")


if __name__ == "__main__":
    unittest.main()
