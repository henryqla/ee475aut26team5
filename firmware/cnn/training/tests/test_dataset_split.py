"""
File:        test_dataset_split.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Unit tests for dataset_split.py: how many sessions are held out,
             the three sides never share a session, the same seed gives the
             same split, named sessions are honored and checked, and
             oversampling balances the training takes using only training
             takes (no test take can leak in). Needs numpy.
"""

# ============================================================================
# Imports
# ============================================================================

import unittest

import numpy as np

import helpers  # noqa: F401  (sets sys.path; must come first)
from dataset_split import SplitError, n_held_out, oversample, split_sessions, take_indices

# ============================================================================
# Tests
# ============================================================================


class TestSplit(unittest.TestCase):
    def test_n_held_out(self):
        for n, k in ((3, 1), (9, 1), (10, 2), (14, 2), (15, 3)):
            self.assertEqual(n_held_out(n), k, n)

    def test_disjoint_and_complete(self):
        sessions = ["s%02d" % i for i in range(12)]
        sp = split_sessions(sessions, 475)
        self.assertEqual(len(sp.test), 2)           # 12 // 5
        self.assertEqual(len(sp.val), 2)            # 10 // 5
        self.assertEqual(len(sp.train), 8)
        sides = [set(sp.train), set(sp.val), set(sp.test)]
        self.assertEqual(set().union(*sides), set(sessions))
        self.assertEqual(sum(len(s) for s in sides), len(sessions))
        for side in (sp.train, sp.val, sp.test):
            self.assertEqual(side, sorted(side))

    def test_three_sessions_is_leave_one_out(self):
        sp = split_sessions(["a", "b", "c"], 475)
        self.assertEqual((len(sp.train), len(sp.val), len(sp.test)), (1, 1, 1))

    def test_same_seed_same_split(self):
        sessions = ["s%d" % i for i in range(7)]
        self.assertEqual(split_sessions(sessions, 475), split_sessions(list(reversed(sessions)),
                                                                       475))

    def test_needs_three_sessions(self):
        with self.assertRaises(SplitError):
            split_sessions(["a", "b"], 475)

    def test_named_sessions(self):
        sp = split_sessions(["a", "b", "c", "d"], 475, test=["d"], val=["c"])
        self.assertEqual((sp.train, sp.val, sp.test), (["a", "b"], ["c"], ["d"]))
        sp = split_sessions(["a", "b", "c", "d"], 475, test=["d"])
        self.assertEqual(sp.test, ["d"])
        self.assertNotIn("d", sp.val + sp.train)

    def test_named_sessions_checked(self):
        with self.assertRaises(SplitError):
            split_sessions(["a", "b", "c"], 475, test=["x"])
        with self.assertRaises(SplitError):
            split_sessions(["a", "b", "c"], 475, test=["a"], val=["a"])
        with self.assertRaises(SplitError):     # Nothing left for validation
            split_sessions(["a", "b", "c"], 475, test=["a", "b"])

    def test_take_indices(self):
        np.testing.assert_array_equal(take_indices(["a", "b", "a", "c"], ["a", "c"]), [0, 2, 3])

    def test_oversample_balances_training_only(self):
        labels = np.array([0, 0, 0, 0, 1, 1, 2, 0, 1, 2])   # Takes 7..9 are test takes
        train = np.arange(7)
        out = oversample(train, labels, 3, np.random.default_rng(475))
        np.testing.assert_array_equal(np.bincount(labels[out], minlength=3), [4, 4, 4])
        self.assertTrue(set(out.tolist()) <= set(train.tolist()))   # No test take
        self.assertTrue(set(train.tolist()) <= set(out.tolist()))   # Every original kept
        again = oversample(train, labels, 3, np.random.default_rng(475))
        np.testing.assert_array_equal(out, again)

    def test_oversample_class_without_takes_stays_empty(self):
        labels = np.array([0, 0, 0, 1])
        out = oversample(np.arange(4), labels, 3, np.random.default_rng(1))
        np.testing.assert_array_equal(np.bincount(labels[out], minlength=3), [3, 3, 0])

    def test_oversample_empty(self):
        out = oversample(np.array([], dtype=np.int64), np.array([0, 1]), 3,
                         np.random.default_rng(1))
        self.assertEqual(out.size, 0)


if __name__ == "__main__":
    unittest.main()
