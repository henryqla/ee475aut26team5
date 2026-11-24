"""
File:        evaluate.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.1.0
Change:      0.1.0 (training-live-inference-handoff.md, request E): new file.
Description: Tests a trained model the way the board will run it (request E,
             "Testing the way the board runs"). NumPy only: the model comes in
             as a predict function, so the tests run without TensorFlow.
             - window_scores(): confusion matrix and recall per class on cut
               windows (top class, no cutoff).
             - scan_starts() / scan_clips(): the board's sliding window over
               whole clips. A window of `width` rows ends every `hop` rows, the
               first ending at row width - 1 (the board needs a full window
               first). A window across lost readings, a reset or ok=0 rows is
               skipped, as the board skips it (F5).
             - detections(): the board's rule (O1): the top class is not NONE
               and its probability is at least the cutoff. After a detection,
               the next is allowed only once at least the lockout (2 s, owner
               2026-11-24) has passed, counted in samples. A tie goes to the
               first class (argmax), and the board must do the same.
             - event_outcome(): one event clip's result: exactly one correct
               detection, any wrong-class detection, a double count, or none.
             - cutoff_table(): for each cutoff x, the share of held-out event
               clips with each outcome, and false alarms per hour over the
               held-out NONE clips. Nothing is picked (P8).
             Formatting helpers turn the results into report lines.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

import windows as wn

# ============================================================================
# Constants
# ============================================================================

# Outcomes of one event clip at one cutoff, in report order.
ONCE = "once"            # Exactly one detection, of the clip's class
WRONG = "wrong class"    # At least one detection of the other event class
DOUBLE = "double"        # Two or more detections of the clip's class, none wrong
MISSED = "missed"        # No detection at all
OUTCOMES = (ONCE, WRONG, DOUBLE, MISSED)

# Windows handed to predict() at once by scan_clips() (~41 MB at 832 x 3).
PREDICT_CHUNK = 4096

# ============================================================================
# Classes
# ============================================================================


@dataclass
class WindowScores:
    """Window scores: top class against the true class."""
    matrix: np.ndarray      # (K, K) counts: row = true class, column = top class
    recall: list            # Per class: share of its windows called right, or None


@dataclass
class ClipData:
    """One whole clip to scan."""
    clip_id: str            # "<session>/<file>"
    label: str              # Its class name
    model: np.ndarray       # (rows, 3) z_hp, roll, pitch
    n: Optional[np.ndarray]     # (rows,) sample counts, or None (older takes)
    ok: Optional[np.ndarray]    # (rows,) bool, or None
    rate_hz: float


@dataclass
class ScannedClip:
    """One clip after the board's sliding window."""
    clip_id: str
    label: str
    duration_s: float       # rows / rate
    ends: np.ndarray        # Row of each scanned window's last sample
    probs: np.ndarray       # (len(ends), K) model outputs
    skipped: int            # Windows skipped as damaged
    rate_hz: float


@dataclass
class CutoffRow:
    """Streaming results at one cutoff."""
    cutoff: float
    outcomes: dict = field(default_factory=dict)    # Outcome -> event clips, all classes
    per_class: dict = field(default_factory=dict)   # Event class -> {outcome: clips}
    false_alarms: int = 0                           # Detections in NONE clips
    none_hours: float = 0.0                         # Total length of the NONE clips scanned
    fa_per_hour: Optional[float] = None             # None: no NONE clip was scanned

    @property
    def events(self) -> int:
        """@return Event clips counted."""
        return sum(self.outcomes.values())

# ============================================================================
# Functions: window scores
# ============================================================================


def window_scores(y_true: np.ndarray, probs: np.ndarray, n_classes: int) -> WindowScores:
    """
    @param y_true     (windows,) true class indexes.
    @param probs      (windows, n_classes) model outputs.
    @param n_classes  Number of classes.
    @return Confusion matrix and recall per class (None for a class with no windows).
    """
    matrix = np.zeros((n_classes, n_classes), dtype=np.int64)
    pred = np.argmax(probs, axis=1) if len(probs) else np.zeros(0, dtype=np.int64)
    for t, p in zip(np.asarray(y_true, dtype=np.int64), pred):
        matrix[t, p] += 1
    recall = []
    for k in range(n_classes):
        total = int(matrix[k].sum())
        recall.append(None if total == 0 else float(matrix[k, k]) / total)
    return WindowScores(matrix=matrix, recall=recall)

# ============================================================================
# Functions: the board's sliding window
# ============================================================================


def scan_starts(rows: int, n: Optional[np.ndarray], ok: Optional[np.ndarray], width: int,
                hop: int) -> tuple:
    """
    The windows the board would run on a clip.
    @param rows   Rows in the clip.
    @param n      (rows,) sample counts, or None.
    @param ok     (rows,) bool, or None.
    @param width  Window length, rows.
    @param hop    Rows between windows.
    @return (kept start rows as an int64 array, number skipped as damaged).
    """
    if rows < width:
        return np.zeros(0, dtype=np.int64), 0
    starts = np.arange(0, rows - width + 1, hop, dtype=np.int64)
    kept = [int(s) for s in starts if not wn.damage(n, ok, int(s), int(s) + width)]
    return np.array(kept, dtype=np.int64), len(starts) - len(kept)


def scan_clips(clips: list, predict: Callable, width: int, hop: int,
               n_classes: int) -> list:
    """
    Runs the model over every clip's board windows, in chunks of PREDICT_CHUNK.
    @param clips      ClipData list.
    @param predict    Function: (m, width, 3) float32 -> (m, n_classes) probabilities.
    @param width      Window length, rows.
    @param hop        Rows between windows.
    @param n_classes  Number of classes.
    @return One ScannedClip per clip, in the same order.
    """
    plans = []
    for c in clips:
        starts, skipped = scan_starts(c.model.shape[0], c.n, c.ok, width, hop)
        plans.append((starts, skipped))

    # Gather every window, predict in chunks, then hand the rows back per clip.
    owners = [(i, s) for i, (starts, _) in enumerate(plans) for s in starts]
    probs = np.zeros((len(owners), n_classes), dtype=np.float32)
    for lo in range(0, len(owners), PREDICT_CHUNK):
        part = owners[lo:lo + PREDICT_CHUNK]
        x = np.stack([clips[i].model[s:s + width] for i, s in part]).astype(np.float32)
        probs[lo:lo + len(part)] = predict(x)
    out, pos = [], 0
    for c, (starts, skipped) in zip(clips, plans):
        k = len(starts)
        out.append(ScannedClip(clip_id=c.clip_id, label=c.label,
                               duration_s=c.model.shape[0] / c.rate_hz,
                               ends=starts + width - 1, probs=probs[pos:pos + k],
                               skipped=skipped, rate_hz=c.rate_hz))
        pos += k
    return out


def detections(ends: np.ndarray, probs: np.ndarray, rate_hz: float, cutoff: float,
               lockout_s: float, none_index: int) -> list:
    """
    The board's detection rule over one clip's scanned windows.
    @param ends        Row of each window's last sample, increasing.
    @param probs       (len(ends), K) model outputs.
    @param rate_hz     Sample rate (turns the lockout into rows).
    @param cutoff      Smallest top-class probability that counts.
    @param lockout_s   After a detection, the next is allowed once at least
                       this long has passed.
    @param none_index  Index of the NONE class.
    @return (end row, class index, probability) of each detection.
    """
    lockout_rows = int(round(lockout_s * rate_hz))
    out = []
    last = None
    for end, p in zip(ends, probs):
        k = int(np.argmax(p))
        conf = float(p[k])
        if k == none_index or conf < cutoff:
            continue
        if last is not None and int(end) - last < lockout_rows:
            continue
        out.append((int(end), k, conf))
        last = int(end)
    return out


def event_outcome(dets: list, label_index: int) -> str:
    """
    @param dets         detections() of one event clip.
    @param label_index  The clip's class index.
    @return ONCE, WRONG, DOUBLE or MISSED.
    """
    correct = sum(1 for _, k, _ in dets if k == label_index)
    wrong = len(dets) - correct
    if wrong > 0:
        return WRONG
    if correct == 0:
        return MISSED
    return ONCE if correct == 1 else DOUBLE


def cutoff_table(scanned: list, classes: tuple, none_label: str, cutoffs: tuple,
                 lockout_s: float) -> list:
    """
    Streaming results at each cutoff. Clips with no scannable window are left
    out (the report lists how many).
    @param scanned     ScannedClip list (held-out clips).
    @param classes     Class names in output order.
    @param none_label  Name of the NONE class.
    @param cutoffs     Cutoffs to try.
    @param lockout_s   The board's lockout, s.
    @return One CutoffRow per cutoff.
    """
    none_index = classes.index(none_label)
    events = [c for c in scanned if c.label != none_label and len(c.ends) > 0]
    nones = [c for c in scanned if c.label == none_label and len(c.ends) > 0]
    hours = sum(c.duration_s for c in nones) / 3600.0
    rows = []
    for x in cutoffs:
        row = CutoffRow(cutoff=float(x), outcomes={o: 0 for o in OUTCOMES})
        for c in events:
            o = event_outcome(detections(c.ends, c.probs, c.rate_hz, x, lockout_s, none_index),
                              classes.index(c.label))
            row.outcomes[o] += 1
            row.per_class.setdefault(c.label, {k: 0 for k in OUTCOMES})[o] += 1
        row.false_alarms = sum(len(detections(c.ends, c.probs, c.rate_hz, x, lockout_s,
                                              none_index)) for c in nones)
        row.none_hours = hours
        row.fa_per_hour = (row.false_alarms / hours) if nones else None
        rows.append(row)
    return rows

# ============================================================================
# Functions: report lines
# ============================================================================


def _pct(part: int, whole: int) -> str:
    """@return part / whole as a percentage with one decimal, or "-" when whole is 0."""
    return "-" if whole == 0 else "%.1f%%" % (100.0 * part / whole)


def format_scores(ws: WindowScores, classes: tuple) -> list:
    """
    @param ws       Window scores.
    @param classes  Class names in output order.
    @return Report lines: the confusion matrix, then recall per class.
    """
    w = max(len(c) for c in classes) + 2
    lines = ["Confusion matrix (rows: true class, columns: top class):",
             " " * w + "".join(c.rjust(w) for c in classes)]
    for k, c in enumerate(classes):
        lines.append(c.ljust(w) + "".join(str(int(v)).rjust(w) for v in ws.matrix[k]))
    lines.append("Recall per class:")
    for k, c in enumerate(classes):
        total = int(ws.matrix[k].sum())
        r = ws.recall[k]
        lines.append("  %-*s %s" % (w, c, "missing (no windows)" if r is None
                                    else "%.1f%% (%d/%d)" % (100 * r, ws.matrix[k, k], total)))
    return lines


def format_cutoff_table(rows: list, event_classes: tuple) -> list:
    """
    @param rows           cutoff_table() output.
    @param event_classes  The event class names (POTHOLE, SPEED_BUMP), for the
                          per-class "once" columns.
    @return Report lines: one row per cutoff.
    """
    if not rows:
        return []
    heads = ["cutoff", ONCE] + ["%s %s" % (c, ONCE) for c in event_classes] + \
            [WRONG, DOUBLE, MISSED, "false alarms/h"]
    widths = [max(8, len(h) + 2) for h in heads]
    lines = ["".join(h.rjust(wd) for h, wd in zip(heads, widths))]
    for r in rows:
        cells = ["%.2f" % r.cutoff, _pct(r.outcomes[ONCE], r.events)]
        for c in event_classes:
            pc = r.per_class.get(c, {})
            cells.append(_pct(pc.get(ONCE, 0), sum(pc.values())))
        cells += [_pct(r.outcomes[WRONG], r.events), _pct(r.outcomes[DOUBLE], r.events),
                  _pct(r.outcomes[MISSED], r.events),
                  "missing" if r.fa_per_hour is None else "%.1f" % r.fa_per_hour]
        lines.append("".join(v.rjust(wd) for v, wd in zip(cells, widths)))
    return lines
