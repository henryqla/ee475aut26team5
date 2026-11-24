"""
File:        windows.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.0
Change:      0.1.0 (manual-recording-overhaul-handoff.md, request C): new file.
Description: Turns saved clips into fixed-length training windows each time
             training runs. Clip files are only read, never changed. NumPy
             only. Reads clips with the record GUI's own reader
             (clip_file.load_clip), so the GUI and training share one parser.

             Input: every clip_v1 file in the chosen sessions
             (<data_dir>/<session>/*.csv; _deleted/ is never read), plus older
             624-row snapshot-era takes when the window is OLD_TAKE_MAX_WINDOW_S
             or shorter. The label comes from the file name, so relabels in
             the GUI count.

             Finding the bump (POTHOLE and SPEED_BUMP clips), as the handoff
             says: each channel (z_hp, roll, pitch) is divided by its median
             absolute value in the clip (over its ok rows); the score is the
             sum of the three squared scaled values, 0 on ok=0 rows, smoothed
             with a PEAK_SMOOTH_S moving average; the peak is its highest
             point. This is the only place an event's position is estimated.

             Event windows: up to K_PER_EVENT windows of WINDOW_S per event
             clip, each placing the peak at a random point between PEAK_LO and
             PEAK_HI of the window. The start positions are all different
             (a clip with room for fewer gives fewer, never repeats), drawn
             with a generator seeded from WINDOW_SEED and the clip's
             session/file name, so adding clips never changes another clip's
             windows and two runs give identical arrays.
             NONE windows: every NONE_HOP_S through every NONE clip, short or
             long. Nothing is cut from the quiet parts of event clips.
             Damaged windows (a jump in n, a reset, or ok=0 rows) are skipped
             and listed (W2); no other window is drawn in their place.
             Optional decimation (C5): a windowed-sinc low-pass FIR runs over
             the whole clip, then every DECIMATE-th sample is kept; the damage
             check then also covers half the filter's length around a window.

             Output (WindowSet): X (windows x samples x 3, z_hp / roll / pitch,
             float32), y (class index in cfg.CLASSES order), the session and
             clip ID of every window (for session-wise splits, W3), and one
             report row per file, written as windows_report.csv. Below
             MIN_CLIPS_PER_CLASS or MIN_SESSIONS it warns, never refuses (W4).

             Command line (from this folder; see README.md for both shells):
               python windows.py [--sessions S ...] [--window-s 2.0]
                                 [--decimate 1] [--data-dir DIR] [--out DIR]
                                 [--no-npz]
             It writes windows_report.csv and windows.npz to
             runs/windows_<date_time>/ (git-ignored) unless --out says where.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import argparse
import csv
import datetime
import math
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE.parent, HERE.parent / "cnn_record_gui"):   # cnn_config; clip_file, take_store
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cnn_config as cfg  # noqa: E402  (needs the path above)
from clip_file import KIND_TAKE, LoadedClip, load_clip  # noqa: E402
from take_store import TakeStore, parse_take_name  # noqa: E402

# ============================================================================
# Constants
# ============================================================================

# Channel order of X's last axis.
CHANNELS = ("z_hp", "roll", "pitch")

# Labels whose clips give event windows; NONE clips give NONE windows.
EVENT_LABELS = ("POTHOLE", "SPEED_BUMP")
NONE_LABEL = "NONE"

# Damage reasons, in the order they are checked (one reason per window).
REASON_RESET = "reset"
REASON_GAP = "gap"
REASON_OK0 = "ok0"

# windows_report.csv columns.
REPORT_COLUMNS = ("session", "file", "label", "kind", "source", "rows", "peak_s",
                  "windows_cut", "windows_skipped", "skip_reasons", "note")

# ============================================================================
# Classes
# ============================================================================


@dataclass
class WindowOptions:
    """The cutter's settings. Defaults come from cnn_config (request C)."""
    window_s: float = cfg.WINDOW_S
    k_per_event: int = cfg.K_PER_EVENT
    peak_lo: float = cfg.PEAK_LO
    peak_hi: float = cfg.PEAK_HI
    seed: int = cfg.WINDOW_SEED
    none_hop_s: float = cfg.NONE_HOP_S
    smooth_s: float = cfg.PEAK_SMOOTH_S
    decimate: int = cfg.DECIMATE
    old_take_max_window_s: float = cfg.OLD_TAKE_MAX_WINDOW_S
    min_clips_per_class: int = cfg.MIN_CLIPS_PER_CLASS
    min_sessions: int = cfg.MIN_SESSIONS


@dataclass
class ClipReport:
    """One row of windows_report.csv: what one file gave."""
    session: str
    file: str
    label: str = ""
    kind: str = ""                   # clip, take, or "" if it could not be read
    source: str = ""                 # manual, long, take
    rows: int = 0
    peak_s: Optional[float] = None   # Event clips: peak time from the clip's start (row / rate)
    windows_cut: int = 0
    skipped: dict = field(default_factory=dict)   # Damage reason -> windows skipped
    note: str = ""

    def as_row(self) -> list:
        """@return The CSV row, in REPORT_COLUMNS order."""
        return [self.session, self.file, self.label, self.kind, self.source, self.rows,
                "" if self.peak_s is None else "%.3f" % self.peak_s, self.windows_cut,
                sum(self.skipped.values()),
                ";".join("%s:%d" % kv for kv in sorted(self.skipped.items())), self.note]


@dataclass
class WindowSet:
    """Everything one run of the cutter produced."""
    X: np.ndarray                 # (windows, samples, 3) float32
    y: np.ndarray                 # (windows,) int64, index into cfg.CLASSES
    sessions: np.ndarray          # (windows,) str: each window's session
    clip_ids: np.ndarray          # (windows,) str: "<session>/<file>"
    rate_hz: Optional[float]      # Sample rate of X (after decimation); None if no window
    report: list                  # ClipReport per file
    warnings: list                # Printed warnings (W4 minimums)

# ============================================================================
# Functions: signal
# ============================================================================


def find_peak(model: np.ndarray, ok: Optional[np.ndarray], rate_hz: float,
              smooth_s: float) -> Optional[int]:
    """
    The handoff's peak: per-channel median-absolute scaling, sum of squares,
    0 on ok=0 rows, moving average of smooth_s, highest point.
    @param model     (rows, 3) z_hp, roll, pitch.
    @param ok        (rows,) bool, or None (older takes: every row ok).
    @param rate_hz   Sample rate.
    @param smooth_s  Moving-average length, s.
    @return The peak's row index, or None if the clip has no ok rows.
    """
    rows = model.shape[0]
    use = np.ones(rows, dtype=bool) if ok is None else ok.astype(bool)
    if rows == 0 or not use.any():
        return None
    scale = np.median(np.abs(model[use]), axis=0)
    scale = np.where(np.isfinite(scale) & (scale > 0), scale, 1.0)   # A flat channel: unscaled
    score = np.sum((model / scale) ** 2, axis=1)
    score[~use] = 0.0
    k = min(rows, max(1, int(round(smooth_s * rate_hz))))
    smooth = np.convolve(score, np.ones(k) / k, mode="same")
    return int(np.argmax(smooth))


def lowpass_taps(factor: int) -> np.ndarray:
    """
    Anti-alias filter for decimating by factor: a windowed-sinc low-pass FIR
    (Hamming window), cutting off at DECIMATE_CUTOFF_FRAC of the new Nyquist
    frequency, unity gain at 0 Hz.
    Method: https://en.wikipedia.org/wiki/Sinc_filter (windowed-sinc design).
    @param factor  Decimation factor, >= 2.
    @return The taps (odd count).
    """
    n_taps = cfg.DECIMATE_TAPS_PER_FACTOR * factor + 1
    fc = cfg.DECIMATE_CUTOFF_FRAC / factor          # Cutoff as a fraction of the old Nyquist
    m = np.arange(n_taps) - (n_taps - 1) / 2.0
    taps = fc * np.sinc(fc * m) * np.hamming(n_taps)
    return taps / taps.sum()


def damage(n: Optional[np.ndarray], ok: Optional[np.ndarray], start: int, stop: int,
           margin: int = 0) -> str:
    """
    @param n       (rows,) sample counts, or None (older takes).
    @param ok      (rows,) bool, or None.
    @param start   First row of the window.
    @param stop    One past its last row.
    @param margin  Rows checked on each side as well (the filter's reach).
    @return REASON_RESET, REASON_GAP or REASON_OK0 if the window crosses one,
            else "".
    """
    rows = len(n) if n is not None else (len(ok) if ok is not None else stop)
    lo, hi = max(0, start - margin), min(rows, stop + margin)
    if n is not None and hi - lo > 1:
        d = np.diff(n[lo:hi])
        if np.any(d <= 0):
            return REASON_RESET
        if np.any(d > 1):
            return REASON_GAP
    if ok is not None and not ok[lo:hi].all():
        return REASON_OK0
    return ""

# ============================================================================
# Functions: cutting
# ============================================================================


def _event_starts(peak: int, rows: int, width: int, opts: WindowOptions,
                  clip_id: str) -> np.ndarray:
    """
    @param peak     Peak row.
    @param rows     Rows in the clip.
    @param width    Window length in rows.
    @param opts     Settings.
    @param clip_id  "<session>/<file>", for the clip's own random generator.
    @return Sorted, distinct start rows (up to k_per_event) placing the peak
            between peak_lo and peak_hi of the window; empty if none fits.
    """
    lo_pos = int(math.ceil(opts.peak_lo * width))
    hi_pos = int(math.floor(opts.peak_hi * width))
    first = max(0, peak - hi_pos)
    last = min(rows - width, peak - lo_pos)
    if last < first:
        return np.zeros(0, dtype=np.int64)
    candidates = np.arange(first, last + 1, dtype=np.int64)
    rng = np.random.default_rng([opts.seed, zlib.crc32(clip_id.encode("utf-8"))])
    k = min(opts.k_per_event, len(candidates))
    return np.sort(rng.choice(candidates, size=k, replace=False))


def cut_clip(clip: LoadedClip, clip_id: str, opts: WindowOptions,
             rep: ClipReport) -> list:
    """
    Cuts one clip's windows. Never changes the clip.
    @param clip     A loaded clip or older take with a class label.
    @param clip_id  "<session>/<file>".
    @param opts     Settings.
    @param rep      Its report row, filled in.
    @return Windows as (samples, 3) float32 arrays.
    """
    rows = clip.rows
    width = int(round(opts.window_s * clip.rate_hz))
    if rows < width:
        rep.note = "shorter than the window (%d < %d rows)" % (rows, width)
        return []
    data = clip.model
    margin = 0
    if opts.decimate > 1:
        taps = lowpass_taps(opts.decimate)
        if rows < len(taps):
            rep.note = "shorter than the anti-alias filter"
            return []
        data = np.stack([np.convolve(clip.model[:, c], taps, mode="same")
                         for c in range(clip.model.shape[1])], axis=1)
        margin = len(taps) // 2
    if clip.label in EVENT_LABELS:
        peak = find_peak(clip.model, clip.ok, clip.rate_hz, opts.smooth_s)
        if peak is None:
            rep.note = "no ok rows: no peak"
            return []
        rep.peak_s = peak / clip.rate_hz
        starts = _event_starts(peak, rows, width, opts, clip_id)
        if len(starts) == 0:
            rep.note = "no window fits with the peak between %d%% and %d%%" % (
                round(100 * opts.peak_lo), round(100 * opts.peak_hi))
        elif len(starts) < opts.k_per_event:
            rep.note = "only %d distinct positions fit" % len(starts)
    else:
        hop = max(1, int(round(opts.none_hop_s * clip.rate_hz)))
        starts = np.arange(0, rows - width + 1, hop, dtype=np.int64)
    out = []
    for start in starts:
        start = int(start)
        reason = damage(clip.n, clip.ok, start, start + width, margin)
        if reason:
            rep.skipped[reason] = rep.skipped.get(reason, 0) + 1
            continue
        out.append(data[start:start + width:opts.decimate].astype(np.float32))
    rep.windows_cut = len(out)
    return out


def _clip_files(data_dir: Path, session: str) -> list:
    """
    @param data_dir  The data folder.
    @param session   A session name.
    @return The session folder's *.csv files, sorted by name (never _deleted/).
    """
    folder = Path(data_dir) / session
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.glob("*.csv") if p.is_file())


def build_windows(data_dir: Path = cfg.DATA_DIR, sessions: Optional[list] = None,
                  opts: Optional[WindowOptions] = None) -> WindowSet:
    """
    Cuts every chosen session's clips into windows.
    @param data_dir  Folder holding one folder per session.
    @param sessions  Session names (default: every session in data_dir).
    @param opts      Settings (default: cnn_config's).
    @return The WindowSet. Nothing on disk is changed.
    """
    opts = opts or WindowOptions()
    if opts.decimate < 1:
        raise ValueError("decimate must be 1 or more, not %r" % opts.decimate)
    if not 0.0 <= opts.peak_lo <= opts.peak_hi <= 1.0:
        raise ValueError("need 0 <= peak_lo <= peak_hi <= 1")
    data_dir = Path(data_dir)
    if sessions is None:
        sessions = TakeStore(data_dir).sessions()
    report, windows, labels, win_sessions, win_ids = [], [], [], [], []
    rate = None
    for session in sorted(sessions):
        for path in _clip_files(data_dir, session):
            rep = ClipReport(session=session, file=path.name)
            report.append(rep)
            parsed = parse_take_name(path)
            if parsed is None:
                rep.note = "name is not <LABEL>-<n>.csv: skipped"
                continue
            rep.label = parsed[0]
            try:
                clip = load_clip(path)
            except (OSError, ValueError) as exc:
                rep.note = "unreadable: %s" % exc
                continue
            rep.kind, rep.source, rep.rows = clip.kind, clip.source, clip.rows
            if clip.label not in cfg.CLASSES:
                rep.note = "label %s is not one of %s: relabel it in the record GUI" % (
                    clip.label, ", ".join(cfg.CLASSES))
                continue
            if clip.kind == KIND_TAKE and opts.window_s > opts.old_take_max_window_s:
                rep.note = "older take: used only with windows of %.1f s or less" % (
                    opts.old_take_max_window_s)
                continue
            if rate is not None and clip.rate_hz != rate:
                rep.note = "rate %g Hz differs from %g Hz" % (clip.rate_hz, rate)
                continue
            clip_id = "%s/%s" % (session, path.name)
            cut = cut_clip(clip, clip_id, opts, rep)
            if cut:
                rate = clip.rate_hz
            windows += cut
            labels += [cfg.CLASSES.index(clip.label)] * len(cut)
            win_sessions += [session] * len(cut)
            win_ids += [clip_id] * len(cut)

    if windows:
        X = np.stack(windows).astype(np.float32)
    else:
        width = int(round(opts.window_s * (rate or cfg.DEFAULT_RATE_HZ)))
        X = np.zeros((0, len(range(0, width, opts.decimate)), len(CHANNELS)), dtype=np.float32)
    return WindowSet(X=X, y=np.array(labels, dtype=np.int64),
                     sessions=np.array(win_sessions, dtype=str),
                     clip_ids=np.array(win_ids, dtype=str),
                     rate_hz=None if rate is None else rate / opts.decimate,
                     report=report, warnings=minimum_warnings(report, opts))


def minimum_warnings(report: list, opts: WindowOptions) -> list:
    """
    W4: warns, never refuses. Counts clips that gave at least one window.
    @param report  ClipReport rows.
    @param opts    Settings (the minimums).
    @return Warning lines (empty if both minimums are met).
    """
    per_class = {c: 0 for c in cfg.CLASSES}
    sessions = set()
    for rep in report:
        if rep.windows_cut > 0:
            per_class[rep.label] += 1
            sessions.add(rep.session)
    out = []
    thin = ["%s %d" % (c, k) for c, k in per_class.items() if k < opts.min_clips_per_class]
    if thin:
        out.append("WARNING: fewer than %d clips for %s: held-out scores will be noisy"
                   % (opts.min_clips_per_class, ", ".join(thin)))
    if len(sessions) < opts.min_sessions:
        out.append("WARNING: windows from %d session(s), fewer than %d: held-out scores "
                   "will be noisy" % (len(sessions), opts.min_sessions))
    return out


def write_report(path: Path, report: list) -> None:
    """
    @param path    windows_report.csv to write.
    @param report  ClipReport rows.
    """
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(REPORT_COLUMNS)
        for rep in report:
            w.writerow(rep.as_row())


def summary(ws: WindowSet) -> str:
    """
    @param ws  A WindowSet.
    @return A few lines for the console.
    """
    counts = ", ".join("%s %d" % (c, int(np.sum(ws.y == k))) for k, c in enumerate(cfg.CLASSES))
    skipped = sum(sum(r.skipped.values()) for r in ws.report)
    notes = sum(1 for r in ws.report if r.note and r.windows_cut == 0)
    return ("%d windows of %d samples at %s Hz (%s) from %d file(s); %d window(s) skipped as "
            "damaged; %d file(s) gave no window (see windows_report.csv)"
            % (len(ws.y), ws.X.shape[1], "-" if ws.rate_hz is None else "%g" % ws.rate_hz,
               counts, len(ws.report), skipped, notes))

# ============================================================================
# Command line
# ============================================================================


def main(argv: Optional[list] = None) -> int:
    """
    @param argv  Arguments (default: sys.argv[1:]).
    @return Exit code: 0.
    """
    ap = argparse.ArgumentParser(description="Cut training windows from saved clips "
                                             "(clip files are never changed).")
    ap.add_argument("--data-dir", type=Path, default=cfg.DATA_DIR,
                    help="folder with one folder per session (default: firmware/cnn/data)")
    ap.add_argument("--sessions", nargs="*", default=None,
                    help="sessions to use (default: all)")
    ap.add_argument("--window-s", type=float, default=cfg.WINDOW_S,
                    help="window length, s (default %(default)s)")
    ap.add_argument("--decimate", type=int, default=cfg.DECIMATE,
                    help="decimation factor (default %(default)s = none)")
    ap.add_argument("--out", type=Path, default=None,
                    help="output folder (default: runs/windows_<date_time>)")
    ap.add_argument("--no-npz", action="store_true", help="write only the report")
    args = ap.parse_args(argv)

    opts = WindowOptions(window_s=args.window_s, decimate=args.decimate)
    ws = build_windows(args.data_dir, args.sessions, opts)
    out = args.out or cfg.TRAIN_RUNS_DIR / ("windows_" + datetime.datetime.now()
                                            .strftime("%Y-%m-%d_%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    write_report(out / "windows_report.csv", ws.report)
    if not args.no_npz:
        np.savez_compressed(out / "windows.npz", X=ws.X, y=ws.y, sessions=ws.sessions,
                            clip_ids=ws.clip_ids, classes=np.array(cfg.CLASSES),
                            channels=np.array(CHANNELS),
                            rate_hz=np.array(ws.rate_hz if ws.rate_hz else 0.0))
    print(summary(ws))
    for line in ws.warnings:
        print(line)
    print("Written to %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
