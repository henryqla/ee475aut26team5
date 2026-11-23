"""
File:        eval_metrics.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Scores held-out takes (cnn-record-train-handoff.md v0.4,
             "Evaluation"): confusion matrix, per-class recall and accuracy for
             the CNN, and the same for the rule-based class saved in each
             take's header (the baseline). Rule names are mapped with
             cfg.RULE_TO_CLASS (BUMP -> SPEED_BUMP); a rule UNKNOWN or a
             missing rule class is "no answer" and counts as wrong for every
             class (owner, 2026-11-22). The rule never answers FALSE_TRIGGER.
             Replaces sklearn's confusion_matrix and accuracy_score used by
             the framework, so training needs only numpy and TensorFlow.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

from typing import Optional

import numpy as np

import cnn_config as cfg

# ============================================================================
# Constants
# ============================================================================

# Column name for the rule's "no answer" (UNKNOWN or missing) in its matrix.
NO_ANSWER = "no answer"

# ============================================================================
# Functions
# ============================================================================


def confusion_matrix(y_true: np.ndarray, y_pred: np.ndarray, n_classes: int,
                     n_cols: Optional[int] = None) -> np.ndarray:
    """
    @param y_true     True class index per take.
    @param y_pred     Predicted index per take; may be n_classes ("no answer")
                      when n_cols is n_classes + 1.
    @param n_classes  Number of classes (rows).
    @param n_cols     Number of columns (default n_classes).
    @return int matrix, rows = true class, columns = predicted.
    """
    n_cols = n_classes if n_cols is None else n_cols
    cm = np.zeros((n_classes, n_cols), dtype=np.int64)
    for t, p in zip(np.asarray(y_true), np.asarray(y_pred)):
        cm[int(t), int(p)] += 1
    return cm


def per_class_recall(cm: np.ndarray) -> list:
    """
    @param cm  Confusion matrix (rows true; the first columns are the classes).
    @return Recall per class (correct / takes of that class), or None for a
            class with no takes.
    """
    out = []
    for c in range(cm.shape[0]):
        total = int(cm[c].sum())
        out.append(None if total == 0 else float(cm[c, c]) / total)
    return out


def accuracy(cm: np.ndarray) -> Optional[float]:
    """
    @param cm  Confusion matrix.
    @return Share of takes on the diagonal, or None if there are no takes.
    """
    total = int(cm.sum())
    if total == 0:
        return None
    return float(sum(cm[c, c] for c in range(cm.shape[0]))) / total


def rule_predictions(rule_classes: list, class_names: tuple = cfg.CLASSES) -> np.ndarray:
    """
    @param rule_classes  The take headers' rule_class values (POTHOLE, BUMP,
                         UNKNOWN, missing, ...).
    @param class_names   Training classes, in output order.
    @return Class index per take, or len(class_names) for "no answer".
    """
    no_answer = len(class_names)
    out = []
    for name in rule_classes:
        mapped = cfg.RULE_TO_CLASS.get(name)
        out.append(class_names.index(mapped) if mapped in class_names else no_answer)
    return np.array(out, dtype=np.int64)


def fmt_share(value: Optional[float]) -> str:
    """
    @param value  A share from 0 to 1, or None.
    @return "0.875", or "n/a" for None.
    """
    return "n/a" if value is None else "%.3f" % value


def matrix_markdown(cm: np.ndarray, row_names: list, col_names: list) -> str:
    """
    @param cm         Confusion matrix.
    @param row_names  True class names.
    @param col_names  Predicted class names (one per column).
    @return A Markdown table: rows true, columns predicted.
    """
    lines = ["| true \\ predicted | " + " | ".join(col_names) + " |",
             "| --- |" + " ---: |" * len(col_names)]
    for name, row in zip(row_names, cm):
        lines.append("| %s | %s |" % (name, " | ".join(str(int(v)) for v in row)))
    return "\n".join(lines)


def matrix_text(cm: np.ndarray, row_names: list, col_names: list) -> list:
    """
    @param cm         Confusion matrix.
    @param row_names  True class names.
    @param col_names  Predicted class names.
    @return Lines of a fixed-width table for the run log.
    """
    width = max(len(n) for n in list(row_names) + list(col_names) + ["true \\ pred"]) + 2
    lines = ["true \\ pred".ljust(width) + "".join(n.rjust(width) for n in col_names)]
    for name, row in zip(row_names, cm):
        lines.append(name.ljust(width) + "".join(str(int(v)).rjust(width) for v in row))
    return lines
