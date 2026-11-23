"""
File:        dataset_split.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Adapted from: the idea of balance_classes (top every class up to the largest
             with random copies) in Python/training_widget.py of
             ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Changed: it
             runs after the split, on the training takes only, so no copy of a
             take can land in the test set (the framework balanced before its
             split, which inflated test accuracy); numpy instead of sklearn.
Description: Splits takes by whole sessions (C11, owner 2026-11-22): test
             sessions, validation sessions (early stopping) and training
             sessions, so takes of one pothole never sit on both sides. Then
             oversamples the training takes so every class has as many as the
             largest. Needs numpy only.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

import cnn_config as cfg

# ============================================================================
# Classes
# ============================================================================


class SplitError(ValueError):
    """The sessions cannot be split as asked."""


@dataclass
class SessionSplit:
    """Session names on each side of the split, each list sorted."""
    train: list
    val: list
    test: list
    chosen_by: str   # "seed 475", "named" or a mix, for the report

# ============================================================================
# Functions
# ============================================================================


def n_held_out(n_sessions: int) -> int:
    """
    @param n_sessions  Sessions to pick from.
    @return How many to hold out: one in cfg.SPLIT_HOLDOUT_EVERY, at least one.
    """
    return max(1, n_sessions // cfg.SPLIT_HOLDOUT_EVERY)


def split_sessions(sessions: list, seed: int, test: Optional[list] = None,
                   val: Optional[list] = None) -> SessionSplit:
    """
    Chooses the test, validation and training sessions. Sessions not named
    are picked with the seed: first n_held_out(all) test sessions, then
    n_held_out(rest) validation sessions from the rest.
    @param sessions  Every session that has usable takes.
    @param seed      Random seed (cfg.TRAIN_SEED).
    @param test      Test sessions named by the user, or None.
    @param val       Validation sessions named by the user, or None.
    @return The SessionSplit.
    @raise SplitError if there are too few sessions, a named session has no
           usable takes, the named lists overlap, or no session is left to train.
    """
    sessions = sorted(set(sessions))
    if len(sessions) < cfg.SPLIT_MIN_SESSIONS:
        raise SplitError(
            "Splitting by session needs at least %d sessions with usable takes (one each to "
            "train, validate and test); found %d: %s. Record more sessions."
            % (cfg.SPLIT_MIN_SESSIONS, len(sessions), ", ".join(sessions) or "none"))
    test = sorted(set(test)) if test else []
    val = sorted(set(val)) if val else []
    for what, named in (("Test", test), ("Validation", val)):
        unknown = [s for s in named if s not in sessions]
        if unknown:
            raise SplitError("%s session %s has no usable takes (or does not exist)"
                             % (what, ", ".join(unknown)))
    both = sorted(set(test) & set(val))
    if both:
        raise SplitError("Session %s is named for both test and validation" % ", ".join(both))

    rng = np.random.default_rng(seed)
    how = []
    if not test:
        pool = [s for s in sessions if s not in val]
        k = min(n_held_out(len(sessions)), max(0, len(pool) - 1))
        test = sorted(str(s) for s in rng.choice(pool, size=k, replace=False)) if k else []
        how.append("test by seed %d" % seed)
    else:
        how.append("test named")
    if not val:
        pool = [s for s in sessions if s not in test]
        k = min(n_held_out(len(pool)), max(0, len(pool) - 1))
        val = sorted(str(s) for s in rng.choice(pool, size=k, replace=False)) if k else []
        how.append("validation by seed %d" % seed)
    else:
        how.append("validation named")
    train = [s for s in sessions if s not in test and s not in val]
    if not test or not val or not train:
        raise SplitError("No session left for %s (test %s, validation %s)" % (
            "training" if not train else ("testing" if not test else "validation"),
            ", ".join(test) or "none", ", ".join(val) or "none"))
    return SessionSplit(train=train, val=val, test=test, chosen_by=", ".join(how))


def take_indices(take_sessions: list, chosen: list) -> np.ndarray:
    """
    @param take_sessions  The session of each take, in take order.
    @param chosen         Session names.
    @return Indices of the takes from those sessions, ascending.
    """
    wanted = set(chosen)
    return np.array([i for i, s in enumerate(take_sessions) if s in wanted], dtype=np.int64)


def oversample(indices: np.ndarray, labels: np.ndarray, n_classes: int,
               rng: np.random.Generator) -> np.ndarray:
    """
    Random oversampling of the training takes only (C11): every class that
    has takes is topped up with random copies of its own takes (with
    replacement) to the size of the largest class. Every original take is
    kept once. A class with no training take stays at zero.
    @param indices    Indices of the training takes.
    @param labels     Class index of every take (all takes, not just training).
    @param n_classes  Number of classes.
    @param rng        Seeded generator.
    @return Training indices with the copies added, shuffled.
    """
    indices = np.asarray(indices, dtype=np.int64)
    if indices.size == 0:
        return indices
    own = labels[indices]
    counts = np.bincount(own, minlength=n_classes)
    target = counts.max()
    parts = [indices]
    for c in range(n_classes):
        if 0 < counts[c] < target:
            parts.append(rng.choice(indices[own == c], size=target - counts[c], replace=True))
    out = np.concatenate(parts)
    rng.shuffle(out)
    return out
