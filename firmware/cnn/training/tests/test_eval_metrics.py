"""
File:        test_eval_metrics.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for eval_metrics.py: confusion matrix, per-class
             recall (n/a for a class with no takes), accuracy, and the rule
             baseline: BUMP read as SPEED_BUMP, UNKNOWN and missing as "no
             answer" (wrong for every class), so FALSE_TRIGGER recall is 0.
             Needs numpy.
"""

# ============================================================================
# Imports
# ============================================================================

import unittest

import numpy as np

import helpers  # noqa: F401  (sets sys.path; must come first)
from eval_metrics import (accuracy, confusion_matrix, fmt_share, matrix_markdown,
                          per_class_recall, rule_predictions)

# ============================================================================
# Tests
# ============================================================================


class TestMetrics(unittest.TestCase):
    def test_confusion_recall_accuracy(self):
        cm = confusion_matrix([0, 0, 1, 1, 1], [0, 1, 1, 1, 0], 3)
        self.assertEqual(cm.tolist(), [[1, 1, 0], [1, 2, 0], [0, 0, 0]])
        recall = per_class_recall(cm)
        self.assertAlmostEqual(recall[0], 0.5)
        self.assertAlmostEqual(recall[1], 2 / 3)
        self.assertIsNone(recall[2])
        self.assertAlmostEqual(accuracy(cm), 0.6)
        self.assertIsNone(accuracy(np.zeros((3, 3), dtype=int)))

    def test_rule_baseline(self):
        pred = rule_predictions(["POTHOLE", "BUMP", "UNKNOWN", "missing"])
        self.assertEqual(pred.tolist(), [0, 1, 3, 3])
        # True: pothole, bump, bump (rule UNKNOWN), false trigger (rule POTHOLE).
        cm = confusion_matrix([0, 1, 1, 2], rule_predictions(["POTHOLE", "BUMP", "UNKNOWN",
                                                              "POTHOLE"]), 3, 4)
        self.assertEqual(cm.tolist(), [[1, 0, 0, 0], [0, 1, 0, 1], [1, 0, 0, 0]])
        self.assertEqual(per_class_recall(cm), [1.0, 0.5, 0.0])
        self.assertAlmostEqual(accuracy(cm), 0.5)

    def test_formatting(self):
        self.assertEqual(fmt_share(None), "n/a")
        self.assertEqual(fmt_share(0.875), "0.875")
        text = matrix_markdown(np.array([[1, 2], [3, 4]]), ["A", "B"], ["A", "B"])
        lines = text.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertEqual(lines[2], "| A | 1 | 2 |")


if __name__ == "__main__":
    unittest.main()
