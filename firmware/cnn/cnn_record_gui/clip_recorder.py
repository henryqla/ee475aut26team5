"""
File:        clip_recorder.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.0
Change:      0.1.0 (manual-recording-overhaul-handoff.md, request B): new file.
             Replaces manual_record.py (git tag pre-manual-overhaul): no
             alignment, no cut, no raw copy, and nothing ever refuses to save.
Description: The recording logic behind the P / B / N and L keys, with no Qt,
             so it is unit-tested. Works in the GUI thread on the reader's
             batches of L samples.
             - It always keeps the last MANUAL_PRE_S of L samples.
             - press() starts a clip: the press sample is the last n received
               (the newest buffered sample), and MANUAL_POST_S more device
               samples are collected after it. A jump in n uses up the samples
               it skipped, so a clip spans the same device time with or
               without losses; after a reset each row counts as one.
             - The clip ends when its samples are in, or MANUAL_SAVE_GRACE_S
               after its countdown ends if the stream stopped (tick()), and is
               then saved with whatever arrived (O3). Gaps, resets, ok=0 rows
               and a lost port never stop it; they are counted (ClipFacts).
             - A press during a clip is ignored (O7); so is a press during a
               long recording (B3), and L during a clip.
             - start_long() / stop_long(): a long NONE recording, its rows
               streamed to a file as they arrive, so it has no length limit.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cnn_config as cfg
from clip_file import ClipFacts

# ============================================================================
# Constants
# ============================================================================

# Clip.ended_by values (why a recording ended; all of them are saved).
ENDED_COMPLETE = "complete"      # Every post sample arrived
ENDED_TIMEOUT = "timeout"        # The stream stopped; saved after the grace time
ENDED_STOPPED = "stopped"        # L stopped a long recording
ENDED_CLOSED = "closed"          # Disconnect or the window closing

# ============================================================================
# Classes
# ============================================================================


@dataclass
class Clip:
    """One finished recording, ready to save."""
    source: str                      # cfg.SOURCE_MANUAL or cfg.SOURCE_LONG
    label: str
    press_epoch_ms: int              # Laptop time of the press (long: its start)
    press_n: Optional[int]           # Last sample count received at the press
    press_row: Optional[int]         # Row of that sample in rows (None if none)
    pre_s: float
    post_s: float                    # Long recording: set to its length when it stops
    rate_hz: int
    facts: ClipFacts
    rows: list = field(default_factory=list)   # LogSample, oldest first (short clips)
    body_path: Optional[Path] = None           # Rows file (long recordings)
    stop_epoch_ms: Optional[int] = None        # Long recordings
    ended_by: str = ""

    def rows_text(self) -> str:
        """
        @return The data rows of a short clip, each the L fields as received.
        """
        return "".join(s.row_text() + "\n" for s in self.rows)


class ClipRecorder:
    """The rolling buffer and at most one recording at a time."""

    def __init__(self, pre_s: float = cfg.MANUAL_PRE_S, post_s: float = cfg.MANUAL_POST_S,
                 grace_s: float = cfg.MANUAL_SAVE_GRACE_S,
                 rate_hz: int = cfg.DEFAULT_RATE_HZ) -> None:
        """
        @param pre_s    Seconds kept before a press.
        @param post_s   Seconds collected after it.
        @param grace_s  Seconds after the countdown before a stalled clip is saved.
        @param rate_hz  Sample rate (set_rate() once BOOT says otherwise).
        """
        self._pre_s = pre_s
        self._post_s = post_s
        self._grace_s = grace_s
        self._buf: deque = deque()
        self._cur: Optional[Clip] = None
        self._post_left = 0
        self._press_t = 0.0             # Monotonic time of the press
        self._deadline = 0.0            # Monotonic time a stalled clip is saved
        self._long: Optional[Clip] = None
        self._long_fh = None
        self._long_t0 = 0.0             # Monotonic time the long recording started
        self._last_n: Optional[int] = None
        self.long_error = ""            # First write error of the long recording, if any
        self.set_rate(rate_hz)

    # ------------------------------------------------------------ properties

    @property
    def recording(self) -> bool:
        """@return True while a short clip collects its samples."""
        return self._cur is not None

    @property
    def long_active(self) -> bool:
        """@return True while a long NONE recording runs."""
        return self._long is not None

    @property
    def busy(self) -> bool:
        """@return True while either kind of recording runs."""
        return self._cur is not None or self._long is not None

    @property
    def label(self) -> str:
        """@return The current short clip's label ("" if none)."""
        return self._cur.label if self._cur is not None else ""

    @property
    def rate_hz(self) -> int:
        """@return The sample rate in use."""
        return self._rate

    def remaining_s(self, now: float) -> float:
        """
        @param now  Monotonic time.
        @return Seconds left on the current clip's countdown (0 if none, or
                once the countdown has run out while samples are still due).
        """
        if self._cur is None:
            return 0.0
        return max(0.0, self._press_t + self._post_s - now)

    def long_elapsed_s(self, now: float) -> float:
        """
        @param now  Monotonic time.
        @return Seconds since the long recording started (0 if none).
        """
        return max(0.0, now - self._long_t0) if self._long is not None else 0.0

    def long_rows(self) -> int:
        """@return Rows written to the long recording so far (0 if none)."""
        return self._long.facts.rows if self._long is not None else 0

    # ------------------------------------------------------------ user input

    def set_rate(self, rate_hz: int) -> None:
        """
        @param rate_hz  The board's sample rate (BOOT odr). Resizes the buffer;
                        a clip already recording keeps its own sizes.
        """
        self._rate = max(1, int(rate_hz))
        self._pre_n = max(1, int(round(self._pre_s * self._rate)))
        self._post_n = int(round(self._post_s * self._rate))
        self._buf = deque(self._buf, maxlen=self._pre_n)

    def press(self, label: str, epoch_ms: int, now: float) -> str:
        """
        Starts a clip (P, B or N).
        @param label     Label to save it under.
        @param epoch_ms  Laptop time of the press, ms since the epoch.
        @param now       Monotonic time.
        @return "" if it started, else why the press is ignored.
        """
        if self._long is not None:
            return "a long NONE recording is running: %s ignored until L stops it" % label
        if self._cur is not None:
            return "a clip is recording (%.1f s left): %s ignored" % (self.remaining_s(now), label)
        rows = list(self._buf)
        facts = ClipFacts()
        for s in rows:
            facts.add(s.n, s.ok)
        self._cur = Clip(source=cfg.SOURCE_MANUAL, label=label, press_epoch_ms=epoch_ms,
                         press_n=rows[-1].n if rows else None,
                         press_row=len(rows) - 1 if rows else None,
                         pre_s=self._pre_s, post_s=self._post_s, rate_hz=self._rate,
                         facts=facts, rows=rows)
        self._post_left = self._post_n
        self._press_t = now
        self._deadline = now + self._post_s + self._grace_s
        return ""

    def start_long(self, epoch_ms: int, now: float, body_path: Path) -> str:
        """
        Starts a long NONE recording (L).
        @param epoch_ms   Laptop time of the press, ms.
        @param now        Monotonic time.
        @param body_path  File its rows are written to while it runs.
        @return "" if it started, else why not.
        """
        if self._cur is not None:
            return "a clip is recording (%.1f s left): L ignored" % self.remaining_s(now)
        if self._long is not None:
            return "a long NONE recording is already running"
        try:
            body_path.parent.mkdir(parents=True, exist_ok=True)
            self._long_fh = open(body_path, "w", encoding="utf-8", newline="\n")
        except OSError as exc:
            return "cannot start the long recording: %s" % exc
        self.long_error = ""
        self._long = Clip(source=cfg.SOURCE_LONG, label=cfg.LONG_LABEL, press_epoch_ms=epoch_ms,
                          press_n=self._last_n, press_row=None, pre_s=0.0, post_s=0.0,
                          rate_hz=self._rate, facts=ClipFacts(), body_path=body_path)
        self._long_t0 = now
        return ""

    def stop_long(self, epoch_ms: int, ended_by: str = ENDED_STOPPED) -> Optional[Clip]:
        """
        Stops the long recording (L again, Disconnect, or the window closing).
        @param epoch_ms  Laptop time it stopped, ms.
        @param ended_by  ENDED_STOPPED or ENDED_CLOSED.
        @return The finished Clip (its rows are in body_path), or None.
        """
        clip = self._long
        if clip is None:
            return None
        self._long = None
        try:
            self._long_fh.close()
        except OSError as exc:
            self.long_error = self.long_error or str(exc)
        self._long_fh = None
        clip.stop_epoch_ms = epoch_ms
        clip.post_s = clip.facts.span / float(clip.rate_hz)
        clip.ended_by = ended_by
        return clip

    def finish_all(self, epoch_ms: int) -> list:
        """
        Ends every recording at once (Disconnect, or the window closing), so
        what was collected is still saved.
        @param epoch_ms  Laptop time, ms (stop time of a long recording).
        @return The finished Clips (0, 1 or 2).
        """
        out = []
        if self._cur is not None:
            out.append(self._finish(ENDED_CLOSED))
        long_clip = self.stop_long(epoch_ms, ENDED_CLOSED)
        if long_clip is not None:
            out.append(long_clip)
        return out

    def stream_restarted(self) -> None:
        """
        A BOOT line or a reconnect: the buffer starts over, so the next clip
        never begins with samples from before. A recording in progress goes
        on; its reset shows in its facts when n goes down.
        """
        self._buf.clear()
        if self._cur is None:
            self._last_n = None

    # ----------------------------------------------------------- device input

    def feed(self, samples: list) -> Optional[Clip]:
        """
        Adds a batch of L samples (oldest first).
        @param samples  LogSample list.
        @return The finished short Clip if this batch completed it, else None.
                Samples after the end of a clip go to the buffer.
        """
        done = None
        for s in samples:
            restarted = self._last_n is not None and s.n <= self._last_n
            self._last_n = s.n
            if self._cur is not None:
                step = self._cur.facts.add(s.n, s.ok)
                self._cur.rows.append(s)
                self._post_left -= step
                if self._post_left <= 0:
                    done = self._finish(ENDED_COMPLETE)
            if self._long is not None and self._long_fh is not None:
                try:
                    self._long_fh.write(s.row_text() + "\n")
                    self._long.facts.add(s.n, s.ok)
                except OSError as exc:
                    self.long_error = self.long_error or str(exc)
            if restarted:
                self._buf.clear()    # The board reset: no older samples in the next clip
            self._buf.append(s)
        if self._long_fh is not None and samples:
            try:
                self._long_fh.flush()    # A crash loses at most one batch
            except OSError as exc:
                self.long_error = self.long_error or str(exc)
        return done

    def tick(self, now: float) -> Optional[Clip]:
        """
        Called a few times a second: ends a clip whose stream stopped.
        @param now  Monotonic time.
        @return The Clip if its grace time ran out, else None.
        """
        if self._cur is not None and now >= self._deadline:
            return self._finish(ENDED_TIMEOUT)
        return None

    # ---------------------------------------------------------------- helpers

    def _finish(self, ended_by: str) -> Clip:
        """
        @param ended_by  Why it ended (ENDED_*).
        @return The current short clip, now finished.
        """
        clip = self._cur
        self._cur = None
        self._post_left = 0
        clip.ended_by = ended_by
        return clip
