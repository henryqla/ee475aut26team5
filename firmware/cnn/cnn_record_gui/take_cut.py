"""
File:        take_cut.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: The one place a standard take (624 rows: TAKE_N_PRE before the
             aligned sample, TAKE_N_POST from it on) is cut out of full-rate
             samples: L lines from the log build, or a raw manual recording.
             one-press-manual-takes-handoff.md v0.1 (request 8) asks for one
             shared alignment function, used by manual Record
             (manual_record.py) and by cut_windows.py, so both always cut the
             same take from the same data. Rules, from
             drive-log-manual-record-handoff.md v0.2 (request 6):
             - A window is usable only if n has no jump, no two neighbouring
               samples are more than MAX_SAMPLE_GAP_US apart (the firmware's
               snapshot rule), and every row has ok=1. Otherwise it is
               skipped, never padded.
             - Alignment: the first sample where any channel reaches its BOOT
               threshold (|value| >= threshold, as the firmware compares); if
               none, the first at MISS_FRAC of it; if none, the largest peak
               (each channel divided by its threshold, so the three share one
               scale), if it is at least WEAK_FLOOR_FRAC, placed the session's
               typical crossing-to-peak gap after the aligned row: a weak
               take, saved to weak/. The first crossing wins even when its
               window does not fit (M3); a later crossing past the take window
               is reported, not saved.
             - Rows are copied as printed (text), never re-formatted, so a
               window cut at a trigger equals that trigger's snapshot byte for
               byte, and its crc16 is the one the SNE line would carry.
             - A take's contents depend only on its source, its label and the
               typical gap: no cut time and no cutter commit, so re-cutting a
               raw recording gives the same bytes as the GUI saved (MR-4).
             Standard library only.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cnn_config as cfg
from snapshot_protocol import LogSample, crc16_ccitt_false, parse_l_fields
from take_store import RULE_FIELDS, header_text, parse_take_name, read_header

# ============================================================================
# Constants
# ============================================================================

# Alignment kinds, as written in a take header's align field.
ALIGN_TRIGGER = "trigger"        # The firmware's own trigger (EVT n_trig)
ALIGN_THRESHOLD = "threshold"    # First sample at the BOOT thresholds
ALIGN_MISS_FRAC = "miss_frac"    # First sample at MISS_FRAC of them
ALIGN_PEAK = "peak"              # Weak event: its peak placed at the typical gap
ALIGN_RANDOM = "random"          # Background window

# Levels (fraction of the thresholds, kind) tried in order. A manual
# recording tries the thresholds, then MISS_FRAC; a mark with no EVT is
# searched at MISS_FRAC only (handoff, request 6).
MANUAL_LEVELS = ((1.0, ALIGN_THRESHOLD), (cfg.MISS_FRAC, ALIGN_MISS_FRAC))
MARK_LEVELS = ((cfg.MISS_FRAC, ALIGN_MISS_FRAC),)

# Why an Alignment has no take (Alignment.problem_kind).
NOTHING = "nothing"    # Peak below WEAK_FLOOR_FRAC
EDGE = "edge"          # The 624-row window does not fit in the rows
WINDOW = "window"      # The window has a jump, a time gap or an ok=0 row

# ============================================================================
# Functions: BOOT settings
# ============================================================================


def boot_settings(raw: Optional[str]) -> dict:
    """
    @param raw  A BOOT line, or anything else (e.g. "missing (...)").
    @return Its key=value settings, or {} if it is not a BOOT line.
    """
    if not raw or not raw.startswith("BOOT,"):
        return {}
    settings = {}
    for part in raw.split(",")[1:]:
        key, sep, value = part.partition("=")
        if sep:
            settings[key] = value
    return settings


@dataclass(frozen=True)
class Thresholds:
    """The firmware's trigger thresholds, from BOOT (thr_z g, thr_roll and
    thr_pitch dps)."""
    z: float
    roll: float
    pitch: float

    def reaches(self, s: LogSample, level: float) -> bool:
        """
        @param s      A sample.
        @param level  Fraction of the thresholds (1.0 = the thresholds).
        @return True if any model channel's |value| >= level * its threshold,
                the comparison the firmware makes.
        """
        z, roll, pitch = s.model
        return abs(z) >= level * self.z or abs(roll) >= level * self.roll \
            or abs(pitch) >= level * self.pitch

    def frac(self, s: LogSample) -> float:
        """
        @param s  A sample.
        @return Its largest |channel| / threshold (1.0 = at the threshold).
        """
        z, roll, pitch = s.model
        return max(abs(z) / self.z, abs(roll) / self.roll, abs(pitch) / self.pitch)


def thresholds_from_boot(settings: dict) -> Optional[Thresholds]:
    """
    @param settings  BOOT settings (boot_settings()).
    @return The thresholds, or None if any is missing, not a number, or not
            above 0.
    """
    try:
        thr = Thresholds(float(settings["thr_z"]), float(settings["thr_roll"]),
                         float(settings["thr_pitch"]))
    except (KeyError, ValueError):
        return None
    if min(thr.z, thr.roll, thr.pitch) <= 0.0:
        return None
    return thr


def boot_problem(settings: dict) -> str:
    """
    @param settings  BOOT settings.
    @return Why takes cannot be cut with them ("" if they can): a take needs
            the thresholds, odr (its rate) and fw.
    """
    missing = [k for k in ("fw", "odr", "thr_z", "thr_roll", "thr_pitch") if k not in settings]
    if not settings:
        return "no BOOT line (it is sent at reset; reset the board to get one)"
    if missing:
        return "BOOT line lacks %s" % ", ".join(missing)
    if thresholds_from_boot(settings) is None:
        return "BOOT thresholds are not positive numbers"
    try:
        if int(settings["odr"]) <= 0:
            raise ValueError
    except ValueError:
        return "BOOT odr is not a positive whole number"
    return ""

# ============================================================================
# Functions: windows and alignment
# ============================================================================


def window_problem(rows: list, lo: int, hi: int) -> Optional[str]:
    """
    Checks rows[lo:hi] can be a take.
    @param rows  LogSample rows in device order.
    @param lo    First row of the window.
    @param hi    One past its last row.
    @return Why not (the first problem found), or None if it can.
    """
    if lo < 0:
        return "too few rows before (%d missing)" % -lo
    if hi > len(rows):
        return "too few rows after (%d missing)" % (hi - len(rows))
    prev = None
    for k in range(lo, hi):
        s = rows[k]
        if not s.ok:
            return "ok=0 at n %d (gyro bias not ready)" % s.n
        if prev is not None:
            if s.n != prev.n + 1:
                return "jump in n (%d to %d)" % (prev.n, s.n)
            if s.t_us - prev.t_us > cfg.MAX_SAMPLE_GAP_US:
                return "gap in t_us of %d us at n %d" % (s.t_us - prev.t_us, s.n)
        prev = s
    return None


def first_reaching(rows: list, thr: Thresholds, level: float, lo: int = 0,
                   hi: Optional[int] = None) -> Optional[int]:
    """
    @param rows   LogSample rows.
    @param thr    Thresholds.
    @param level  Fraction of the thresholds.
    @param lo     First row searched.
    @param hi     One past the last row searched (default: all).
    @return The first row index in [lo, hi) that reaches level, or None.
    """
    hi = len(rows) if hi is None else min(hi, len(rows))
    for k in range(max(lo, 0), hi):
        if thr.reaches(rows[k], level):
            return k
    return None


def peak_index(rows: list, thr: Thresholds, lo: int = 0,
               hi: Optional[int] = None) -> tuple:
    """
    @param rows  LogSample rows.
    @param thr   Thresholds.
    @param lo    First row searched.
    @param hi    One past the last row searched (default: all).
    @return (index, frac) of the largest |channel| / threshold in [lo, hi),
            the first one on a tie; (None, 0.0) if the range is empty.
    """
    hi = len(rows) if hi is None else min(hi, len(rows))
    best, best_frac = None, 0.0
    for k in range(max(lo, 0), hi):
        f = thr.frac(rows[k])
        if best is None or f > best_frac:
            best, best_frac = k, f
    return best, best_frac


@dataclass
class Alignment:
    """Where a take goes in a run of rows, or why there is none."""
    kind: str = ""                  # ALIGN_THRESHOLD, ALIGN_MISS_FRAC or ALIGN_PEAK
    index: Optional[int] = None     # Row of the aligned sample (row TAKE_N_PRE of the take)
    level: float = 0.0              # Fraction of the thresholds that aligned it
    peak: Optional[int] = None      # Row of the event's peak
    peak_frac: float = 0.0          # That peak as a fraction of the thresholds
    gap: Optional[int] = None       # Peak offset used (ALIGN_PEAK only)
    second: Optional[int] = None    # Row of a later crossing past the take window (M3)
    problem: str = ""               # Why there is no take ("" = there is one)
    problem_kind: str = ""          # NOTHING, EDGE or WINDOW

    @property
    def ok(self) -> bool:
        """@return True if a take can be cut here."""
        return not self.problem


def align(rows: list, thr: Thresholds, levels: tuple, typical_gap: int, lo: int = 0,
          hi: Optional[int] = None) -> Alignment:
    """
    The shared alignment (request 8): the first crossing of each level in
    turn, else the peak if it is above the floor. The take window must fit
    in rows (not only in the searched range) and pass window_problem().
    @param rows         LogSample rows in device order (one run).
    @param thr          Thresholds from BOOT.
    @param levels       (fraction, kind) pairs, tried in order (MANUAL_LEVELS,
                        MARK_LEVELS).
    @param typical_gap  Samples from the aligned row to the peak, for a weak
                        event (typical_peak_gap()).
    @param lo           First row searched.
    @param hi           One past the last row searched (default: all).
    @return The Alignment; check .ok.
    """
    hi = len(rows) if hi is None else min(hi, len(rows))
    al = None
    for level, kind in levels:
        i = first_reaching(rows, thr, level, lo, hi)
        if i is not None:
            al = Alignment(kind=kind, index=i, level=level)
            break
    if al is None:
        p, frac = peak_index(rows, thr, lo, hi)
        if p is None or frac < cfg.WEAK_FLOOR_FRAC:
            return Alignment(peak=p, peak_frac=frac, problem_kind=NOTHING,
                             problem="nothing found: peak %.0f%% of the threshold, below the "
                                     "%.0f%% floor" % (100 * frac, 100 * cfg.WEAK_FLOOR_FRAC))
        al = Alignment(kind=ALIGN_PEAK, index=p - typical_gap, level=frac, peak=p,
                       peak_frac=frac, gap=typical_gap)

    i = al.index
    if i - cfg.TAKE_N_PRE < 0 or i + cfg.TAKE_N_POST > len(rows):
        al.problem_kind = EDGE
        al.problem = ("event too close to the edge: the take needs %d rows before and %d from "
                      "the aligned sample" % (cfg.TAKE_N_PRE, cfg.TAKE_N_POST))
        return al
    bad = window_problem(rows, i - cfg.TAKE_N_PRE, i + cfg.TAKE_N_POST)
    if bad:
        al.problem_kind = WINDOW
        al.problem = bad
        return al
    if al.kind != ALIGN_PEAK:
        al.peak, al.peak_frac = peak_index(rows, thr, i - cfg.TAKE_N_PRE, i + cfg.TAKE_N_POST)
        al.second = first_reaching(rows, thr, al.level, i + cfg.TAKE_N_POST, hi)
    return al


def typical_peak_gap(session_dir: Path) -> tuple:
    """
    The session's typical crossing-to-peak gap (handoff, "Weak events"): the
    median of n_peak - trigger over its event takes (snapshot takes:
    rule_n_peak - trig_idx; takes cut at an EVT: n_peak - n_trig).
    @param session_dir  The session folder.
    @return (gap in samples, number of takes used). The gap is 0 with fewer
            than PEAK_GAP_MIN_TAKES takes.
    """
    gaps = []
    folder = Path(session_dir)
    if folder.is_dir():
        for path in sorted(folder.glob("*.csv")):
            if parse_take_name(path) is None:
                continue
            try:
                h = read_header(path)
            except OSError:
                continue
            source = h.get("source", "snapshot")
            if source == "snapshot":
                pair = (h.get("trig_idx"), h.get("rule_n_peak"))
            elif source == "evt":
                pair = (h.get("n_trig"), h.get("n_peak"))
            else:
                continue
            try:
                gaps.append(int(pair[1]) - int(pair[0]))
            except (TypeError, ValueError):
                continue   # "missing" peak, or no trigger
    if len(gaps) < cfg.PEAK_GAP_MIN_TAKES:
        return 0, len(gaps)
    return int(round(statistics.median(gaps))), len(gaps)

# ============================================================================
# Functions: take files
# ============================================================================


def snapshot_crc(rows: list, lo: int, hi: int) -> str:
    """
    @param rows  LogSample rows.
    @param lo    First row of the take.
    @param hi    One past its last row.
    @return The CRC-16 the firmware's SNE line would carry for these rows
            (over "SNR,i,z_hp,roll,pitch" with the values as printed), as 4
            hex digits.
    """
    crc = 0xFFFF
    for i, k in enumerate(range(lo, hi)):
        line = "SNR,%d,%s,%s,%s" % ((i,) + rows[k].model_text())
        crc = crc16_ccitt_false(line.encode("utf-8"), crc)
    return "%04X" % crc


def take_rows_text(rows: list, lo: int, hi: int) -> str:
    """
    @param rows  LogSample rows.
    @param lo    First row of the take.
    @param hi    One past its last row.
    @return The take's data rows, "z_hp,roll,pitch" exactly as printed.
    """
    return "".join("%s,%s,%s\n" % rows[k].model_text() for k in range(lo, hi))


def take_header(*, label: str, session: str, source: str, source_file: str, src_key: str,
                gui_commit: str, boot_raw: str, rows: list, index: int, align_kind: str,
                n_peak, peak_frac: Optional[float], label_from: str, evt=None,
                evt_missing: str = "", gap: Optional[int] = None, extra: tuple = ()) -> list:
    """
    The header of a take cut from L rows: the snapshot take's keys (so the
    GUI, the loader and typical_peak_gap() read it like any take), then where
    it came from and how it was aligned.
    @param label        Take label.
    @param session      Session name.
    @param source       "evt", "marker_only", "manual" or "background".
    @param source_file  The source file, relative to the session folder.
    @param src_key      Names the source window; a second cut is skipped.
    @param gui_commit   Commit of the GUI that recorded the source.
    @param boot_raw     The BOOT line the source ran with.
    @param rows         The run's LogSample rows.
    @param index        Row of the aligned sample.
    @param align_kind   An ALIGN_* value.
    @param n_peak       The peak's sample count (the EVT's, or found), or "missing".
    @param peak_frac    The peak as a fraction of the thresholds, or None.
    @param label_from   How the label was chosen ("mark", "none", "manual", ...).
    @param evt          The EventLine (EVT takes), or None.
    @param evt_missing  Why there is no EVT (written in the evt field).
    @param gap          The peak offset used (weak takes), or None.
    @param extra        More (key, value) pairs, written before columns.
    @return (key, value) pairs in file order.
    """
    settings = boot_settings(boot_raw)
    lo, hi = index - cfg.TAKE_N_PRE, index + cfg.TAKE_N_POST
    pairs = [("take_format", cfg.TAKE_FORMAT_VERSION),
             ("label", label),
             ("session", session),
             ("source", source),
             ("source_file", source_file),
             ("src_key", src_key),
             ("gui_commit", gui_commit),
             ("fw", settings.get("fw", "missing")),
             ("seq", evt.seq if evt is not None else "none"),
             ("trig_idx", rows[index].n),
             ("rate_hz", settings.get("odr", "missing")),
             ("n_pre", cfg.TAKE_N_PRE),
             ("n_post", cfg.TAKE_N_POST),
             ("n_rows", cfg.TAKE_N_ROWS),
             ("channels", cfg.TAKE_CHANNELS),
             ("crc16", snapshot_crc(rows, lo, hi))]
    for name in RULE_FIELDS:
        pairs.append(("rule_" + name, evt.fields.get(name, "missing") if evt is not None
                      else "missing"))
    pairs.append(("evt", evt.raw if evt is not None else "missing (%s)" % (evt_missing or "no EVT")))
    pairs.append(("boot", boot_raw if boot_raw else "missing"))
    pairs.append(("align", align_kind))
    pairs.append(("align_n", rows[index].n))
    pairs.append(("n_peak", n_peak))
    if peak_frac is not None:
        pairs.append(("peak_frac", "%.3f" % peak_frac))
    if gap is not None:
        pairs.append(("peak_gap", gap))
    pairs.append(("label_from", label_from))
    pairs.extend(extra)
    pairs.append(("columns", cfg.TAKE_CHANNELS.replace("/", ",")))
    return pairs


@dataclass
class CutResult:
    """A take cut from a source, or why there is none."""
    ok: bool
    label: str = ""
    subdir: str = ""                # "" (session folder) or cfg.WEAK_DIR_NAME
    text: str = ""                  # The take file's contents
    header: dict = field(default_factory=dict)
    src_key: str = ""
    alignment: Optional[Alignment] = None
    message: str = ""               # One line for the status card or the report


def describe(al: Alignment, rate_hz: float) -> str:
    """
    @param al       A good Alignment.
    @param rate_hz  Sample rate.
    @return How it was aligned, for the status card or the report.
    """
    if al.kind == ALIGN_THRESHOLD:
        text = "aligned on the threshold crossing"
    elif al.kind == ALIGN_MISS_FRAC:
        text = "aligned at %.0f%% of the threshold" % (100 * al.level)
    elif al.kind == ALIGN_PEAK:
        text = ("weak event (peak %.0f%% of the threshold), peak-aligned into %s/"
                % (100 * al.peak_frac, cfg.WEAK_DIR_NAME))
    else:
        text = "aligned (%s)" % al.kind
    if al.second is not None:
        text += "; a second event %.1f s later was not saved" % ((al.second - al.index) / rate_hz)
    return text

# ============================================================================
# Functions: raw manual recordings
# ============================================================================


def read_raw_recording(path: Path) -> tuple:
    """
    Reads a raw manual recording (manual/<LABEL>-<n>.csv).
    @param path  The file.
    @return (header, rows): the "# key=value" header and the LogSample rows.
    @raise ValueError if a row does not parse; OSError if it cannot be read.
    """
    header, rows = {}, []
    with open(path, "r", encoding="utf-8") as fh:
        for k, line in enumerate(fh, 1):
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("#"):
                key, sep, value = line[1:].strip().partition("=")
                if sep:
                    header[key] = value
                continue
            s = parse_l_fields(line.split(","))
            if s is None:
                raise ValueError("%s line %d: not a recording row: %r" % (path.name, k, line))
            rows.append(s)
    return header, rows


def cut_manual(header: dict, rows: list, source_file: str, typical_gap: int) -> CutResult:
    """
    THE shared cut for a manual recording (request 8), used by manual Record
    right after it writes the raw file, and by cut_windows.py for raw files
    with no take yet.
    @param header       The raw recording's header.
    @param rows         Its rows.
    @param source_file  Its path relative to the session folder (manual/...).
    @param typical_gap  The session's typical crossing-to-peak gap.
    @return The CutResult; .subdir is weak/ for a weak event.
    """
    label = header.get("label", "")
    src_key = header.get("src_key", "")
    if label not in cfg.LABELS:
        return CutResult(False, label=label, src_key=src_key,
                         message="label %r is not one of %s" % (label, ", ".join(cfg.LABELS)))
    if not src_key:
        return CutResult(False, label=label, message="the recording has no src_key")
    boot_raw = header.get("boot", "")
    settings = boot_settings(boot_raw)
    problem = boot_problem(settings)
    if problem:
        return CutResult(False, label=label, src_key=src_key,
                         message="cannot align: %s" % problem)
    thr = thresholds_from_boot(settings)
    rate = int(settings["odr"])

    al = align(rows, thr, MANUAL_LEVELS, typical_gap)
    if not al.ok:
        if al.problem_kind == NOTHING:
            message = "Nothing found near the press (%s)" % al.problem
        elif al.problem_kind == EDGE:
            message = "Event too close to the edge of the recording"
        else:
            message = "Unusable window around the event (%s)" % al.problem
        return CutResult(False, label=label, src_key=src_key, alignment=al, message=message)

    press_pc = header.get("press_pc", "")
    extra = (("press_pc", press_pc),
             ("press_epoch_ms", header.get("press_epoch_ms", "")),
             ("press_n", header.get("press_n", "")),
             ("pc_time_est", press_pc))
    pairs = take_header(label=label, session=header.get("session", ""), source="manual",
                        source_file=source_file, src_key=src_key,
                        gui_commit=header.get("gui_commit", "unknown"), boot_raw=boot_raw,
                        rows=rows, index=al.index, align_kind=al.kind,
                        n_peak=rows[al.peak].n, peak_frac=al.peak_frac, label_from="manual",
                        evt_missing="manual recording", gap=al.gap, extra=extra)
    text = header_text(pairs) + take_rows_text(rows, al.index - cfg.TAKE_N_PRE,
                                               al.index + cfg.TAKE_N_POST)
    return CutResult(True, label=label,
                     subdir=cfg.WEAK_DIR_NAME if al.kind == ALIGN_PEAK else "",
                     text=text, header=dict((k, str(v)) for k, v in pairs), src_key=src_key,
                     alignment=al, message=describe(al, rate))


def cut_manual_file(path: Path, session_dir: Path, typical_gap: int) -> CutResult:
    """
    Reads a raw manual recording and cuts its take (cut_manual()).
    @param path         The raw recording.
    @param session_dir  Its session folder.
    @param typical_gap  The session's typical crossing-to-peak gap.
    @return The CutResult (not ok if the file cannot be read).
    """
    path = Path(path)
    try:
        source_file = path.relative_to(session_dir).as_posix()
    except ValueError:
        source_file = path.name
    try:
        header, rows = read_raw_recording(path)
    except (OSError, ValueError) as exc:
        return CutResult(False, message="cannot read %s: %s" % (path.name, exc))
    return cut_manual(header, rows, source_file, typical_gap)
