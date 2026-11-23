"""
File:        drive_log.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: The drive log (drive-log-manual-record-handoff.md v0.2, request
             5, "Drive log"). One file per Start log, at
             <session>/drives/drive-<n>.log, never overwritten. It starts with
             '#' header lines (log format version, session, GUI commit, port,
             PC start time, and the connection's latest BOOT line: a log
             usually starts after BOOT went by, and the cutter needs its
             thresholds and fw); after that every line from the board is written
             exactly as received (the bytes, before any decoding), including
             BOOT, the board's own '#' headers, L, EVT, SEG, HLT and FLT. The
             GUI adds two line types of its own:
               PC,<epoch_ms>,<last_n>,<note>     once per PC_LINE_PERIOD_S and
                                                 at start, stop, lost, reconnect
               MARK,<epoch_ms>,<last_n>,<label>  a P / B / F press, or
                                                 manual_<LABEL> for a Record press
             epoch_ms is the laptop's clock; last_n the latest sample count
             seen (-1 if none yet). Laptop time lags the board by the USB and
             batching delay, so these lines label events but never align
             windows. Only the reader thread (serial_handler.py) writes, so a
             busy GUI never delays the log; it is flushed at least every
             LOG_FLUSH_S, so a crash loses at most about that much.

             StreamStats counts what the status line shows: L lines, missing
             samples (jumps in n within a run; a reset starts a new run and
             is never counted as a gap) and timing gaps (n continuous but
             samples more than MAX_SAMPLE_GAP_US apart).
             Standard library only.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cnn_config as cfg
from take_store import iso_time

# ============================================================================
# Constants
# ============================================================================

# Drive log file names: drive-<n>.log.
_LOG_NAME_RE = re.compile(r"^drive-(\d+)\.log$")

# ============================================================================
# Functions
# ============================================================================


def next_log_number(drives_dir: Path) -> int:
    """
    @param drives_dir  A session's drives/ folder (may not exist yet).
    @return One more than the highest drive-<n>.log number in it.
    """
    highest = 0
    if Path(drives_dir).is_dir():
        for p in Path(drives_dir).iterdir():
            m = _LOG_NAME_RE.match(p.name)
            if m:
                highest = max(highest, int(m.group(1)))
    return highest + 1

# ============================================================================
# Classes
# ============================================================================


class StreamStats:
    """Counts L samples, jumps in n and timing gaps, run by run."""

    def __init__(self) -> None:
        self.l_lines = 0        # L samples seen
        self.missing = 0        # Samples lost: jumps in n within a run
        self.timing_gaps = 0    # n continuous, but a time gap over MAX_SAMPLE_GAP_US
        self.runs = 0           # Runs started (first sample, BOOT, n going backwards)
        self._last = None       # (n, t_us) of the previous sample in this run

    @property
    def last_n(self) -> int:
        """@return The latest sample count seen in this run, or -1."""
        return self._last[0] if self._last is not None else -1

    def new_run(self) -> None:
        """A BOOT line: n restarts, so the next sample starts a new run."""
        self._last = None

    def add(self, n: int, t_us: int) -> None:
        """
        @param n     The sample's count.
        @param t_us  Its device time, us.
        """
        self.l_lines += 1
        if self._last is None:
            self.runs += 1
        else:
            last_n, last_t = self._last
            if n <= last_n:
                self.runs += 1          # Reset without a BOOT line: not a gap
            elif n > last_n + 1:
                self.missing += n - last_n - 1
            elif t_us - last_t > cfg.MAX_SAMPLE_GAP_US:
                self.timing_gaps += 1
        self._last = (n, t_us)


@dataclass
class LogStatus:
    """What the GUI shows about the drive log (a copy; safe across threads)."""
    active: bool                    # Still writing
    path: Optional[Path]            # The file (None if it could not be created)
    elapsed_s: float = 0.0
    size: int = 0                   # Bytes written
    l_rate: Optional[float] = None  # L lines per second since the last status
    missing: int = 0
    timing_gaps: int = 0
    runs: int = 0
    marks: int = 0
    malformed_l: int = 0            # L lines the parser could not read
    free_bytes: Optional[int] = None
    low_disk: bool = False
    error: str = ""                 # Why logging stopped on its own ("" if it did not)


class DriveLog:
    """One drive log file. Used only by the reader thread."""

    def __init__(self, drives_dir: Path, info: dict, wall: Callable[[], float] = time.time,
                 mono: Callable[[], float] = time.monotonic) -> None:
        """
        Creates drives/drive-<n>.log (the next free number; never overwrites)
        and writes the header and the start PC line.
        @param drives_dir  The session's drives/ folder (created if needed).
        @param info        Header values: session, gui_commit, port, boot (the
                           connection's latest BOOT line, or "").
        @param wall        Laptop clock, s since the epoch (tests pass a fake).
        @param mono        Monotonic clock, s (tests pass a fake).
        @raise OSError if the file cannot be created or written.
        """
        self._wall = wall
        self._mono = mono
        drives_dir = Path(drives_dir)
        drives_dir.mkdir(parents=True, exist_ok=True)
        n = next_log_number(drives_dir)
        while True:
            path = drives_dir / ("drive-%d.log" % n)
            try:
                self._fh = open(path, "xb")   # Exclusive: an existing log is never touched
                break
            except FileExistsError:
                n += 1
        self.path = path
        self.stats = StreamStats()
        self.marks = 0
        self.size = 0
        self._start_mono = mono()
        self._next_pc = self._start_mono + cfg.PC_LINE_PERIOD_S
        self._next_flush = self._start_mono + cfg.LOG_FLUSH_S
        self._next_disk = self._start_mono
        self._free: Optional[int] = None
        self._rate_mark = (self._start_mono, 0)    # (time, l_lines) at the last status
        start = wall()
        header = (("drive_log_format", cfg.DRIVE_LOG_FORMAT_VERSION),
                  ("session", info.get("session", "")),
                  ("gui_commit", info.get("gui_commit", "unknown")),
                  ("port", info.get("port", "")),
                  ("pc_start", iso_time(start)),
                  ("pc_start_epoch_ms", int(start * 1000)),
                  ("boot", info.get("boot") or "missing (not seen on this connection)"),
                  ("lines", "board lines exactly as received, plus PC,<epoch_ms>,<last_n>,"
                            "<note> and MARK,<epoch_ms>,<last_n>,<label> from the GUI"))
        for key, value in header:
            self._write(("#%s=%s" % (key, str(value).replace("\n", " "))).encode("utf-8"))
        self.pc("start")

    # ------------------------------------------------------------ public API

    def write_board(self, raw: bytes) -> None:
        """
        @param raw  One line from the board, without its "\\n", byte for byte.
        @raise OSError if writing fails.
        """
        self._write(raw)

    def pc(self, note: str, last_n: Optional[int] = None) -> None:
        """
        Writes a PC line (laptop time and the latest n).
        @param note    start, tick, stop, lost or reconnect.
        @param last_n  Latest sample count; default: the one the stats saw.
        @raise OSError if writing fails.
        """
        n = self.stats.last_n if last_n is None else last_n
        self._write(("PC,%d,%d,%s" % (int(self._wall() * 1000), n, note)).encode("ascii"))

    def mark(self, epoch_ms: int, last_n: int, label: str) -> None:
        """
        Writes a MARK line.
        @param epoch_ms  Laptop time of the key press, ms since the epoch.
        @param last_n    Latest sample count the GUI had seen at the press.
        @param label     A label, or manual_<LABEL> for a Record press.
        @raise OSError if writing fails.
        """
        self._write(("MARK,%d,%d,%s" % (epoch_ms, last_n, label)).encode("ascii"))
        self.marks += 1

    def tick(self) -> None:
        """
        Called often by the reader thread: a PC line every PC_LINE_PERIOD_S,
        a flush every LOG_FLUSH_S.
        @raise OSError if writing fails.
        """
        now = self._mono()
        if now >= self._next_pc:
            self._next_pc += cfg.PC_LINE_PERIOD_S
            if self._next_pc <= now:               # Far behind (PC busy): no burst of lines
                self._next_pc = now + cfg.PC_LINE_PERIOD_S
            self.pc("tick")
        if now >= self._next_flush:
            self._fh.flush()
            self._next_flush = now + cfg.LOG_FLUSH_S

    def close(self) -> None:
        """Writes the stop PC line and closes the file. Safe to call twice."""
        if self._fh is None:
            return
        try:
            self.pc("stop")
        finally:
            self._fh.close()
            self._fh = None

    def abandon(self) -> None:
        """Closes the file after a write error, without writing more."""
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None

    def status(self, malformed_l: int = 0, error: str = "") -> LogStatus:
        """
        @param malformed_l  L lines the parser ignored so far.
        @param error        Why logging stopped, if it did.
        @return A snapshot of the log's state for the GUI. Also measures the
                L line rate since the previous call and, every
                DISK_CHECK_PERIOD_S, the free disk space.
        """
        now = self._mono()
        t0, lines0 = self._rate_mark
        rate = (self.stats.l_lines - lines0) / (now - t0) if now - t0 > 0.2 else None
        if rate is not None:
            self._rate_mark = (now, self.stats.l_lines)
        if now >= self._next_disk:
            self._next_disk = now + cfg.DISK_CHECK_PERIOD_S
            try:
                self._free = shutil.disk_usage(self.path.parent).free
            except OSError:
                self._free = None
        return LogStatus(active=self._fh is not None, path=self.path,
                         elapsed_s=now - self._start_mono, size=self.size, l_rate=rate,
                         missing=self.stats.missing, timing_gaps=self.stats.timing_gaps,
                         runs=self.stats.runs, marks=self.marks, malformed_l=malformed_l,
                         free_bytes=self._free,
                         low_disk=self._free is not None and self._free < cfg.LOW_DISK_BYTES,
                         error=error)

    # ---------------------------------------------------------------- helpers

    def _write(self, data: bytes) -> None:
        """
        @param data  One line without its ending; "\\n" is added.
        @raise OSError if writing fails, or the log is closed.
        """
        if self._fh is None:
            raise OSError("drive log is closed")
        self._fh.write(data + b"\n")
        self.size += len(data) + 1
