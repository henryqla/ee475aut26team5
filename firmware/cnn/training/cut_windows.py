"""
File:        cut_windows.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.2.0
Change:      0.2.0 (owner, 2026-11-23; changes L6/M2): nothing is written to
             weak/ any more and there is no floor. A marker_only or manual
             take that reaches no level is peak-aligned into the session
             folder like any other, however weak (take_cut.py 0.2.0). Takes
             already in weak/ still count as cut.
             0.1.1: a run with no BOOT line (missed at a reset, or a log with
             no BOOT in its header) takes its settings from the first CFG line
             in it: the board repeats BOOT's settings as CFG every
             CFG_PERIOD_MS. A CFG never starts a run.
Description: Cuts standard takes (624 rows: 104 before and 520 from the
             aligned sample) out of a session's drive logs and manual
             recordings (drive-log-manual-record-handoff.md v0.2, request 6;
             one-press-manual-takes-handoff.md v0.1, request 8). The record
             GUI and the training loader read them like any other take. The
             alignment and the take format come from the record GUI's
             take_cut.py, the same code manual Record uses, so both always
             cut the same take from the same data. Standard library only.

             Usage, from firmware/cnn/training/ (see README.md, Part 6):
               python cut_windows.py --session 2026-11-25_car_route1
             Options: --data-dir, --background N (default
             BACKGROUND_PER_LOG; 0 = none), --seed (default BACKGROUND_SEED).

             What it makes, in this order:
             1. Manual recordings (<session>/manual/*.csv) that have no take
                yet: cut by take_cut.cut_manual_file(). Manual Record already
                cut most of them; their src_key matches, so they are skipped.
             2. From each drive log (<session>/drives/*.log, split into runs
                at each BOOT line or where n goes backwards; a run without a
                BOOT uses its first CFG line's settings), one take per
                EVT, at n_trig, exactly where the firmware's snapshot would
                be. Its label is the last MARK within MARK_MATCH_S after the
                trigger, or UNLABELED. An EVT inside a manual recording that
                has a take is skipped as covered by it (owner, 2026-11-22).
             3. One take per MARK with no EVT (marker_only): aligned at
                MISS_FRAC in the MARK_MATCH_S before the mark, else on the
                biggest peak there, however small (listed for review only if
                nothing moved at all). Marks inside a manual recording with a
                take are skipped as covered.
             4. Background windows (background/NONE-<n>.csv): N per log,
                picked with the seed, at least BACKGROUND_CLEAR_S from any
                trigger or mark, with no jump in n and every row ok=1. Picked
                once per log: a log that already has background windows gets
                no more, so a second run never adds a second set.

             Rules: a window with a jump in n, a time gap, an ok=0 row or too
             few rows is skipped with its reason, never padded. A window
             whose src_key already exists (in the session folder, weak/ from
             older versions, background/ or their _deleted/ folders) is
             skipped, so running
             twice adds nothing. A take whose settings differ from the
             session's takes is refused and listed. Logs and raw recordings
             are only read, never changed. The script prints a summary and
             writes <session>/cut_report.txt (which also records the commit
             it ran from: takes themselves carry no cut time or cutter commit).
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import argparse
import bisect
import datetime
import random
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
CNN_DIR = HERE.parent
for _p in (HERE, CNN_DIR, CNN_DIR / "cnn_record_gui"):  # This folder, cnn_config, take_cut
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cnn_config as cfg  # noqa: E402  (needs the path above)
import take_cut as tc  # noqa: E402
from snapshot_protocol import (ConfigLine, EventLine, parse_l_fields,  # noqa: E402
                               parse_other_line)
from take_store import (TakeStore, checkout_commit, header_text, iso_time,  # noqa: E402
                        read_header)

# ============================================================================
# Constants
# ============================================================================

_LOG_NAME_RE = re.compile(r"^drive-(\d+)\.log$")

# ============================================================================
# Classes: drive log contents
# ============================================================================


@dataclass
class Mark:
    """A MARK line: a P / B / F press, or manual_<LABEL> for a Record press."""
    epoch_ms: int
    last_n: int
    label: str

    @property
    def manual(self) -> bool:
        """@return True for a Record press's mark."""
        return self.label.startswith("manual_")


@dataclass
class Run:
    """The part of a drive log between two resets: n only grows within it."""
    number: int                                   # 1, 2, ... in the log
    boot_raw: str = ""                            # Its BOOT line, or the one its first CFG
                                                  # repeats ("" if neither was seen)
    rows: list = field(default_factory=list)      # LogSample, in order
    ns: list = field(default_factory=list)        # rows[k].n, for searching
    evts: list = field(default_factory=list)      # EventLine
    marks: list = field(default_factory=list)     # Mark
    pcs: list = field(default_factory=list)       # (epoch_ms, last_n)

    def index_of(self, n: int) -> Optional[int]:
        """
        @param n  A sample count.
        @return The row index holding n, or None if that sample is not here.
        """
        k = bisect.bisect_left(self.ns, n)
        return k if k < len(self.ns) and self.ns[k] == n else None

    def pc_time_est(self, n: int, rate: int) -> str:
        """
        @param n     A sample count in this run.
        @param rate  Sample rate.
        @return Laptop time of sample n, estimated from the PC line before it
                (ISO 8601), or "unknown". Lags the board slightly; display only.
        """
        best = None
        for epoch_ms, last_n in self.pcs:
            if 0 <= last_n <= n and (best is None or last_n > best[1]):
                best = (epoch_ms, last_n)
        if best is None:
            return "unknown"
        return iso_time((best[0] + (n - best[1]) * 1000.0 / rate) / 1000.0)


@dataclass
class DriveLogData:
    """One parsed drive log."""
    path: Path
    header: dict                  # The GUI's '#key=value' header
    runs: list                    # Run
    has_stop: bool                # A "stop" PC line was written (Stop log or Disconnect)
    malformed_l: int              # L lines that did not parse


def log_number(path: Path) -> int:
    """
    @param path  A drive log.
    @return Its <n> from drive-<n>.log (0 if the name is not one).
    """
    m = _LOG_NAME_RE.match(path.name)
    return int(m.group(1)) if m else 0


def read_drive_log(path: Path) -> DriveLogData:
    """
    Parses a drive log. The first run takes the header's BOOT line (the log
    usually starts after BOOT went by); every BOOT line in the log starts a
    run, and so does n going backwards (a reset whose BOOT was lost). A run
    with no BOOT takes the settings of its first CFG line (BOOT's settings,
    repeated by the board); a CFG never starts a run, except before any.
    @param path  The log file.
    @return Its contents.
    @raise OSError if it cannot be read.
    """
    header, runs = {}, []
    pending_pcs, pending_marks = [], []
    cur: Optional[Run] = None
    in_header, has_stop, malformed = True, False, 0

    def new_run(boot_raw: str) -> Run:
        run = Run(len(runs) + 1, boot_raw)
        run.pcs.extend(pending_pcs)
        run.marks.extend(pending_marks)
        pending_pcs.clear()
        pending_marks.clear()
        runs.append(run)
        return run

    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("#"):
                if in_header:
                    key, sep, value = line[1:].partition("=")
                    if sep and "," not in key:      # The board's '#' headers have commas
                        header[key] = value
                continue
            if in_header:
                in_header = False
                boot = header.get("boot", "")
                if boot.startswith("BOOT,"):
                    cur = new_run(boot)
            tag = line.split(",", 1)[0]
            if tag == "L":
                s = parse_l_fields(line.split(",")[1:])
                if s is None:
                    malformed += 1
                    continue
                if cur is None or (cur.ns and s.n <= cur.ns[-1]):
                    cur = new_run("")
                cur.rows.append(s)
                cur.ns.append(s.n)
            elif tag == "BOOT":
                cur = new_run(line)
            elif tag == "CFG":
                item = parse_other_line(line)
                if isinstance(item, ConfigLine):
                    boot = item.as_boot().raw
                    if cur is None:
                        cur = new_run(boot)     # Settings before any sample: the first run
                    elif not cur.boot_raw:
                        cur.boot_raw = boot     # This run's BOOT was missed
            elif tag == "EVT":
                item = parse_other_line(line)
                if isinstance(item, EventLine) and cur is not None:
                    cur.evts.append(item)
            elif tag in ("PC", "MARK"):
                f = line.split(",", 3)
                try:
                    epoch_ms, last_n = int(f[1]), int(f[2])
                except (IndexError, ValueError):
                    continue
                note = f[3] if len(f) > 3 else ""
                if tag == "PC":
                    has_stop = has_stop or note == "stop"
                    (cur.pcs if cur is not None else pending_pcs).append((epoch_ms, last_n))
                else:
                    (cur.marks if cur is not None else pending_marks).append(
                        Mark(epoch_ms, last_n, note))
    return DriveLogData(path, header, runs, has_stop, malformed)

# ============================================================================
# Classes: the cut
# ============================================================================


@dataclass
class CutReport:
    """Everything one run did, for the summary and cut_report.txt."""
    session: str
    made: list = field(default_factory=list)          # (path in the session, label, source, align)
    skipped: list = field(default_factory=list)       # (what, reason)
    review: list = field(default_factory=list)        # (what, reason): nothing found
    covered: list = field(default_factory=list)       # what: inside a manual recording
    refused: list = field(default_factory=list)       # (what, reason): settings differ
    unlabeled_evts: list = field(default_factory=list)   # what
    already: int = 0                                  # Windows whose src_key existed
    notes: list = field(default_factory=list)
    sources: list = field(default_factory=list)       # Lines describing the inputs


class Cutter:
    """Cuts one session. Use run()."""

    def __init__(self, data_dir: Path, session: str, background: int, seed: int) -> None:
        """
        @param data_dir    Folder with one subfolder per session.
        @param session     The session to cut.
        @param background  Background windows per drive log (0 = none).
        @param seed        Seed that picks them.
        @raise ValueError for a bad session name; FileNotFoundError if the
               session folder does not exist.
        """
        self.store = TakeStore(Path(data_dir))
        self.session = session
        self.dir = self.store.session_dir(session)
        if not self.dir.is_dir():
            raise FileNotFoundError("No session folder %s" % self.dir)
        self.background = max(0, int(background))
        self.seed = int(seed)
        self.report = CutReport(session)
        self.keys = self.store.src_keys(session)
        self._reference = self._existing_settings()

    # ---------------------------------------------------------------- settings

    @staticmethod
    def _settings_of(header: dict) -> tuple:
        """
        @param header  A take header.
        @return (RUN_SETTINGS_KEYS values, BOOT_SETTINGS_KEYS values or None).
        """
        run = tuple(str(header.get(k, "missing")) for k in cfg.RUN_SETTINGS_KEYS)
        boot = tc.boot_settings(header.get("boot", ""))
        return run, (tuple(boot.get(k, "?") for k in cfg.BOOT_SETTINGS_KEYS) if boot else None)

    def _existing_settings(self) -> Optional[dict]:
        """
        @return {"run": set of run tuples, "boot": set of boot tuples} of the
                session's takes, or None if it has none yet.
        """
        runs, boots = set(), set()
        for path in self.store.list_takes(self.session):
            try:
                run, boot = self._settings_of(read_header(path))
            except OSError:
                continue
            runs.add(run)
            if boot is not None:
                boots.add(boot)
        return {"run": runs, "boot": boots} if runs else None

    def _settings_problem(self, header: dict) -> str:
        """
        The loader's check, made before a take is written (CUT-4): it must
        share the settings of the session's takes.
        @param header  The new take's header.
        @return Why it is refused ("" if it is not). The first accepted take
                of an empty session sets the reference.
        """
        run, boot = self._settings_of(header)
        ref = self._reference
        if ref is None:
            self._reference = {"run": {run}, "boot": {boot} if boot else set()}
            return ""
        if len(ref["run"]) > 1 or len(ref["boot"]) > 1:
            return "the session already holds takes with different settings"
        if run not in ref["run"]:
            return "settings %s differ from the session's %s" % (
                ", ".join("%s=%s" % kv for kv in zip(cfg.RUN_SETTINGS_KEYS, run)),
                ", ".join("%s=%s" % kv for kv in zip(cfg.RUN_SETTINGS_KEYS, next(iter(ref["run"])))))
        if boot is not None:
            if ref["boot"] and boot not in ref["boot"]:
                return "BOOT settings %s differ from the session's" % (
                    ", ".join("%s=%s" % kv for kv in zip(cfg.BOOT_SETTINGS_KEYS, boot)))
            ref["boot"].add(boot)
        return ""

    def _write(self, what: str, label: str, text: str, header: dict, subdir: str,
               src_key: str, source: str, align: str) -> bool:
        """
        Writes one cut take after the settings check.
        @return True if it was written.
        """
        problem = self._settings_problem(header)
        if problem:
            self.report.refused.append((what, problem))
            return False
        try:
            path = self.store.write_take(self.session, label, text, subdir=subdir)
        except (OSError, ValueError) as exc:
            self.report.skipped.append((what, "not written: %s" % exc))
            return False
        self.keys.add(src_key)
        self.report.made.append((path.relative_to(self.dir).as_posix(), label, source, align))
        return True

    # --------------------------------------------------------------- sources

    def _manual_recordings(self) -> list:
        """
        @return (path, header) of every raw recording in manual/, by name.
        """
        folder = self.dir / cfg.MANUAL_DIR_NAME
        out = []
        if folder.is_dir():
            for path in sorted(folder.glob("*.csv")):
                try:
                    out.append((path, read_header(path)))
                except OSError as exc:
                    self.report.skipped.append(("%s/%s" % (cfg.MANUAL_DIR_NAME, path.name),
                                                "cannot read: %s" % exc))
        return out

    def _logs(self) -> list:
        """
        @return The session's drive logs, parsed, by number.
        """
        folder = self.dir / cfg.DRIVES_DIR_NAME
        out = []
        if folder.is_dir():
            for path in sorted(folder.glob("drive-*.log"), key=log_number):
                try:
                    out.append(read_drive_log(path))
                except OSError as exc:
                    self.report.skipped.append(("%s/%s" % (cfg.DRIVES_DIR_NAME, path.name),
                                                "cannot read: %s" % exc))
        return out

    # --------------------------------------------------------------- the cut

    def run(self) -> CutReport:
        """
        Cuts everything the session has no take for yet.
        @return The report (also written to <session>/cut_report.txt by main()).
        """
        manual = self._manual_recordings()
        logs = self._logs()
        rep = self.report
        for log in logs:
            rep.sources.append("%s/%s: %d run(s), %d L rows, %d EVT, %d mark(s)%s" % (
                cfg.DRIVES_DIR_NAME, log.path.name, len(log.runs),
                sum(len(r.rows) for r in log.runs), sum(len(r.evts) for r in log.runs),
                sum(len(r.marks) for r in log.runs), "" if log.has_stop else
                " (no stop line: still logging, or the GUI stopped unexpectedly)"))
            if log.malformed_l:
                rep.notes.append("%s: %d malformed L line(s) ignored" % (log.path.name,
                                                                         log.malformed_l))
        rep.sources.append("%s/: %d recording(s)" % (cfg.MANUAL_DIR_NAME, len(manual)))

        # 1. Manual recordings without a take.
        gap, used = tc.typical_peak_gap(self.dir)
        for path, header in manual:
            self._cut_manual(path, header, gap)

        # 2. One take per EVT; covered spans come from manual recordings with a take.
        covered = self._covered_spans(manual)
        consumed = set()      # id() of the marks that labeled an EVT take
        setups = {}
        for log in logs:
            for run in log.runs:
                setups[(log.path.name, run.number)] = setup = self._run_setup(log, run)
                if setup is not None:
                    self._cut_evts(log, run, setup, covered, consumed)

        # 3. Marks with no EVT (the gap now includes this run's EVT takes).
        gap, used = tc.typical_peak_gap(self.dir)
        rep.notes.append("Typical crossing-to-peak gap for peak-aligned takes: %d samples (from %d "
                         "event take(s); 0 below %d)" % (gap, used, cfg.PEAK_GAP_MIN_TAKES))
        for log in logs:
            for run in log.runs:
                setup = setups[(log.path.name, run.number)]
                if setup is not None:
                    self._cut_marks(log, run, setup, covered, consumed, gap)

        # 4. Background windows.
        for log in logs:
            self._cut_background(log)
        return rep

    def _cut_manual(self, path: Path, header: dict, gap: int) -> None:
        """
        @param path    A raw recording.
        @param header  Its header.
        @param gap     Typical crossing-to-peak gap.
        """
        what = "%s/%s" % (cfg.MANUAL_DIR_NAME, path.name)
        key = header.get("src_key", "")
        if key and key in self.keys:
            self.report.already += 1
            return
        result = tc.cut_manual_file(path, self.dir, gap)
        if not result.ok:
            al = result.alignment
            if al is not None and al.problem_kind in (tc.NOTHING, tc.EDGE):
                self.report.review.append((what, result.message))
            else:
                self.report.skipped.append((what, result.message))
            return
        self._write(what, result.label, result.text, result.header, "",
                    result.src_key, "manual", result.alignment.kind)

    def _covered_spans(self, manual: list) -> dict:
        """
        Which samples manual recordings already cover (owner, 2026-11-22):
        a recording whose take exists covers its first_n..last_n in the run
        holding its manual_ mark (matched by press time and n).
        @param manual  (path, header) of the raw recordings.
        @return {(epoch_ms, press_n): (first_n, last_n, what)}.
        """
        spans = {}
        for path, header in manual:
            if header.get("src_key", "") not in self.keys:
                continue    # No take: the log's own takes are still wanted
            try:
                key = (int(header["press_epoch_ms"]), int(header["press_n"]))
                spans[key] = (int(header["first_n"]), int(header["last_n"]),
                              "%s/%s" % (cfg.MANUAL_DIR_NAME, path.name))
            except (KeyError, ValueError):
                continue
        return spans

    @staticmethod
    def _run_spans(run: Run, covered: dict) -> list:
        """
        @param run      A run.
        @param covered  _covered_spans() output.
        @return (first_n, last_n, what) of the manual recordings inside this run.
        """
        return [covered[(m.epoch_ms, m.last_n)] for m in run.marks
                if m.manual and (m.epoch_ms, m.last_n) in covered]

    def _run_setup(self, log: DriveLogData, run: Run) -> Optional[tuple]:
        """
        @param log  The drive log.
        @param run  One of its runs.
        @return (thresholds, rate) for the run, or None if its BOOT line is
                missing or unusable (then one skip names what it held).
        """
        settings = tc.boot_settings(run.boot_raw)
        problem = tc.boot_problem(settings)
        if problem:
            if run.evts or [m for m in run.marks if not m.manual]:
                self.report.skipped.append((
                    "%s run %d" % (log.path.name, run.number),
                    "%d EVT(s) and %d mark(s) not cut: %s" % (
                        len(run.evts), len([m for m in run.marks if not m.manual]), problem)))
            return None
        return tc.thresholds_from_boot(settings), int(settings["odr"])

    def _cut_evts(self, log: DriveLogData, run: Run, setup: tuple, covered: dict,
                  consumed: set) -> None:
        """
        One take per EVT in the run, at its n_trig.
        @param log       The drive log.
        @param run       The run.
        @param setup     (thresholds, rate) of the run.
        @param covered   Manual recordings' spans.
        @param consumed  Collects the marks that labeled a take.
        """
        spans = self._run_spans(run, covered)
        stem = log.path.stem
        thr, rate = setup
        match_n = int(round(cfg.MARK_MATCH_S * rate))
        for evt in run.evts:
            what = "%s run %d EVT seq %d" % (log.path.name, run.number, evt.seq)
            try:
                n_trig = int(evt.fields.get("n_trig", ""))
            except ValueError:
                self.report.skipped.append((what, "EVT has no n_trig (firmware before "
                                                  "0.1.3-bench)"))
                continue
            key = "drive:%s:run%d:evt:%d" % (stem, run.number, n_trig)
            if key in self.keys:
                self.report.already += 1
                continue
            cover = next((s for s in spans if s[0] <= n_trig <= s[1]), None)
            if cover is not None:
                self.report.covered.append("%s (n_trig %d): covered by %s" % (what, n_trig,
                                                                             cover[2]))
                continue
            i = run.index_of(n_trig)
            if i is None:
                self.report.skipped.append((what, "trigger sample n %d is not in the log"
                                                  % n_trig))
                continue
            bad = tc.window_problem(run.rows, i - cfg.TAKE_N_PRE, i + cfg.TAKE_N_POST)
            if bad:
                self.report.skipped.append((what, bad))
                continue
            marks = [m for m in run.marks if not m.manual and m.label in cfg.LABELS
                     and n_trig <= m.last_n <= n_trig + match_n]
            for m in marks:
                consumed.add(id(m))
            label = marks[-1].label if marks else cfg.UNLABELED
            if not marks:
                self.report.unlabeled_evts.append(what)
            _p, frac = tc.peak_index(run.rows, thr, i - cfg.TAKE_N_PRE, i + cfg.TAKE_N_POST)
            extra = [("n_trig", n_trig)]
            if marks:
                extra.append(("marks", ";".join("%s@%d" % (m.label, m.last_n) for m in marks)))
            extra.append(("pc_time_est", run.pc_time_est(n_trig, rate)))
            pairs = tc.take_header(
                label=label, session=self.session, source="evt",
                source_file="%s/%s" % (cfg.DRIVES_DIR_NAME, log.path.name), src_key=key,
                gui_commit=log.header.get("gui_commit", "unknown"), boot_raw=run.boot_raw,
                rows=run.rows, index=i, align_kind=tc.ALIGN_TRIGGER,
                n_peak=evt.fields.get("n_peak", "missing"), peak_frac=frac,
                label_from="mark" if marks else "none", evt=evt, extra=tuple(extra))
            text = header_text(pairs) + tc.take_rows_text(run.rows, i - cfg.TAKE_N_PRE,
                                                          i + cfg.TAKE_N_POST)
            self._write(what, label, text, dict((k, str(v)) for k, v in pairs), "", key, "evt",
                        tc.ALIGN_TRIGGER)

    def _cut_marks(self, log: DriveLogData, run: Run, setup: tuple, covered: dict,
                   consumed: set, gap: int) -> None:
        """
        One take per mark that labeled no EVT take (marker_only).
        @param log       The drive log.
        @param run       The run.
        @param setup     (thresholds, rate) of the run.
        @param covered   Manual recordings' spans.
        @param consumed  Marks that labeled an EVT take.
        @param gap       Typical crossing-to-peak gap.
        """
        spans = self._run_spans(run, covered)
        stem = log.path.stem
        thr, rate = setup
        match_n = int(round(cfg.MARK_MATCH_S * rate))
        for m in run.marks:
            if m.manual or id(m) in consumed:
                continue
            what = "%s run %d MARK %s at n %d" % (log.path.name, run.number, m.label, m.last_n)
            if m.label not in cfg.LABELS:
                self.report.skipped.append((what, "label is not one of %s" % ", ".join(cfg.LABELS)))
                continue
            key = "drive:%s:run%d:mark:%d:%d" % (stem, run.number, m.last_n, m.epoch_ms)
            if key in self.keys:
                self.report.already += 1
                continue
            cover = next((s for s in spans if s[0] <= m.last_n <= s[1] + match_n), None)
            if cover is not None:
                self.report.covered.append("%s: covered by %s" % (what, cover[2]))
                continue
            lo = bisect.bisect_left(run.ns, m.last_n - match_n)
            hi = bisect.bisect_right(run.ns, m.last_n)
            if lo >= hi:
                self.report.skipped.append((what, "no L rows in the %.0f s before the mark"
                                                  % cfg.MARK_MATCH_S))
                continue
            al = tc.align(run.rows, thr, tc.MARK_LEVELS, gap, lo, hi)
            if not al.ok:
                (self.report.review if al.problem_kind == tc.NOTHING
                 else self.report.skipped).append((what, al.problem))
                continue
            extra = (("mark", "%s@%d" % (m.label, m.last_n)),
                     ("miss_frac", cfg.MISS_FRAC),
                     ("thresholds", "z %g g, roll %g dps, pitch %g dps" % (thr.z, thr.roll,
                                                                         thr.pitch)),
                     ("pc_time_est", iso_time(m.epoch_ms / 1000.0)))
            pairs = tc.take_header(
                label=m.label, session=self.session, source="marker_only",
                source_file="%s/%s" % (cfg.DRIVES_DIR_NAME, log.path.name), src_key=key,
                gui_commit=log.header.get("gui_commit", "unknown"), boot_raw=run.boot_raw,
                rows=run.rows, index=al.index, align_kind=al.kind,
                n_peak=run.rows[al.peak].n, peak_frac=al.peak_frac, label_from="mark",
                evt_missing="no EVT: the threshold did not fire", gap=al.gap, extra=extra)
            text = header_text(pairs) + tc.take_rows_text(run.rows, al.index - cfg.TAKE_N_PRE,
                                                          al.index + cfg.TAKE_N_POST)
            # Session folder however weak (owner, 2026-11-23: no weak/).
            self._write(what, m.label, text, dict((k, str(v)) for k, v in pairs), "", key,
                        "marker_only", al.kind)

    def _cut_background(self, log: DriveLogData) -> None:
        """
        Background windows for one log (L8): picked once per log.
        @param log  The drive log.
        """
        if not self.background:
            return
        stem = log.path.stem
        prefix = "drive:%s:" % stem
        if any(k.startswith(prefix) and ":bg:" in k for k in self.keys):
            self.report.notes.append("%s: background windows were picked before; none added"
                                     % log.path.name)
            return
        w = cfg.TAKE_N_ROWS
        candidates = []
        setups = {}
        for run in log.runs:
            settings = tc.boot_settings(run.boot_raw)
            if tc.boot_problem(settings) or len(run.rows) < w:
                continue
            setups[run.number] = (tc.thresholds_from_boot(settings), int(settings["odr"]))
            clear = int(round(cfg.BACKGROUND_CLEAR_S * setups[run.number][1]))
            rows = run.rows
            # Prefix sums of bad rows (ok=0) and bad links (a jump in n or a
            # time gap from the row before), so each window is checked in O(1).
            bad_ok, bad_link = [0], [0, 0]
            for k, s in enumerate(rows):
                bad_ok.append(bad_ok[-1] + (0 if s.ok else 1))
                if k:
                    p = rows[k - 1]
                    link = s.n != p.n + 1 or s.t_us - p.t_us > cfg.MAX_SAMPLE_GAP_US
                    bad_link.append(bad_link[-1] + (1 if link else 0))
            points = sorted([int(e.fields["n_trig"]) for e in run.evts
                             if e.fields.get("n_trig", "").isdigit()] + [m.last_n for m in run.marks])
            for i in range(len(rows) - w + 1):
                if bad_ok[i + w] - bad_ok[i] or bad_link[i + w] - bad_link[i + 1]:
                    continue
                n0, n1 = rows[i].n, rows[i + w - 1].n
                # Every point must be at least `clear` samples from the window.
                k = bisect.bisect_left(points, n0 - clear + 1)
                if k < len(points) and points[k] < n1 + clear:
                    continue
                candidates.append((run.number, i))
        rng = random.Random("%d:%s" % (self.seed, log.path.name))
        rng.shuffle(candidates)
        chosen = []
        for run_no, i in candidates:
            if len(chosen) >= self.background:
                break
            if all(r != run_no or abs(i - j) >= w for r, j in chosen):
                chosen.append((run_no, i))
        if len(chosen) < self.background:
            self.report.notes.append("%s: only %d background window(s) fit (asked for %d)"
                                     % (log.path.name, len(chosen), self.background))
        runs = {r.number: r for r in log.runs}
        for run_no, i in sorted(chosen):
            run = runs[run_no]
            thr, rate = setups[run_no]
            index = i + cfg.TAKE_N_PRE
            key = "drive:%s:run%d:bg:%d" % (stem, run_no, run.rows[i].n)
            p, frac = tc.peak_index(run.rows, thr, i, i + w)
            pairs = tc.take_header(
                label=cfg.BACKGROUND_LABEL, session=self.session, source="background",
                source_file="%s/%s" % (cfg.DRIVES_DIR_NAME, log.path.name), src_key=key,
                gui_commit=log.header.get("gui_commit", "unknown"), boot_raw=run.boot_raw,
                rows=run.rows, index=index, align_kind=tc.ALIGN_RANDOM,
                n_peak=run.rows[p].n, peak_frac=frac, label_from="background",
                evt_missing="background window",
                extra=(("seed", self.seed), ("pc_time_est", run.pc_time_est(run.rows[index].n,
                                                                            rate))))
            text = header_text(pairs) + tc.take_rows_text(run.rows, i, i + w)
            self._write("%s run %d background at n %d" % (log.path.name, run_no, run.rows[i].n),
                        cfg.BACKGROUND_LABEL, text, dict((k, str(v)) for k, v in pairs),
                        cfg.BACKGROUND_DIR_NAME, key, "background", tc.ALIGN_RANDOM)

# ============================================================================
# Functions: report and command line
# ============================================================================


def report_lines(rep: CutReport, commit: str, background: int, seed: int) -> list:
    """
    @param rep         The run's report.
    @param commit      Commit of this checkout.
    @param background  The --background value.
    @param seed        The --seed value.
    @return The text of cut_report.txt, line by line.
    """
    out = ["cut_windows.py report",
           "session: %s" % rep.session,
           "run: %s (commit %s)" % (datetime.datetime.now().astimezone().isoformat(
               timespec="seconds"), commit),
           "options: background %d per log, seed %d" % (background, seed),
           "", "Sources:"]
    out += ["  " + s for s in rep.sources]
    counts = Counter((label, source) for _p, label, source, _a in rep.made)
    out += ["", "Takes made: %d" % len(rep.made)]
    out += ["  %s from %s: %d" % (label, source, n) for (label, source), n in sorted(counts.items())]
    out += ["  %s (align %s)" % (p, a) for p, _l, _s, a in rep.made]
    out.append("Already cut before (src_key exists): %d" % rep.already)

    def section(title: str, items: list) -> None:
        out.extend(["", "%s: %d" % (title, len(items))])
        out.extend("  %s" % (("%s: %s" % it) if isinstance(it, tuple) else it) for it in items)

    section("Skipped (with reasons)", rep.skipped)
    section("For review (nothing found, or too close to the edge)", rep.review)
    section("Covered by a manual recording", rep.covered)
    section("Refused (settings differ from the session)", rep.refused)
    section("EVTs with no mark (saved as UNLABELED)", rep.unlabeled_evts)
    section("Notes", rep.notes)
    return out


def run_cut(data_dir: Path, session: str, background: int = cfg.BACKGROUND_PER_LOG,
            seed: int = cfg.BACKGROUND_SEED, write_report: bool = True) -> CutReport:
    """
    Cuts one session and writes its cut_report.txt.
    @param data_dir      Folder with one subfolder per session.
    @param session       Session name.
    @param background    Background windows per drive log.
    @param seed          Seed that picks them.
    @param write_report  Write <session>/cut_report.txt.
    @return The report.
    """
    cutter = Cutter(data_dir, session, background, seed)
    rep = cutter.run()
    if write_report:
        text = "\n".join(report_lines(rep, checkout_commit(HERE), background, seed)) + "\n"
        TakeStore._write_atomic(cutter.dir / cfg.CUT_REPORT_NAME, text)
    return rep


def main(argv: Optional[list] = None) -> int:
    """
    Command line entry point.
    @param argv  Arguments (default: sys.argv[1:]).
    @return Exit code: 0 done, 2 bad arguments or no such session.
    """
    ap = argparse.ArgumentParser(description="Cut standard takes from a session's drive logs "
                                             "and manual recordings.")
    ap.add_argument("--session", required=True, help="Session folder name, e.g. "
                                                     "2026-11-25_car_route1")
    ap.add_argument("--data-dir", default=str(cfg.DATA_DIR),
                    help="Folder holding the sessions (default: firmware/cnn/data)")
    ap.add_argument("--background", type=int, default=cfg.BACKGROUND_PER_LOG,
                    help="Background windows per drive log; 0 = none (default %(default)s)")
    ap.add_argument("--seed", type=int, default=cfg.BACKGROUND_SEED,
                    help="Seed that picks the background windows (default %(default)s)")
    args = ap.parse_args(argv)
    try:
        rep = run_cut(Path(args.data_dir), args.session, args.background, args.seed)
    except (ValueError, FileNotFoundError) as exc:
        print("cut_windows.py: %s" % exc, file=sys.stderr)
        return 2
    lines = report_lines(rep, checkout_commit(HERE), args.background, args.seed)
    print("\n".join(lines))
    print("\nReport written to %s" % (Path(args.data_dir) / args.session / cfg.CUT_REPORT_NAME))
    return 0


if __name__ == "__main__":
    sys.exit(main())
