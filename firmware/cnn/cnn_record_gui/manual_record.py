"""
File:        manual_record.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Manual Record (drive-log-manual-record-handoff.md v0.2, request
             5; changed by one-press-manual-takes-handoff.md v0.1, request 8).
             The GUI always keeps the last MANUAL_PRE_S of L samples; a press
             collects MANUAL_POST_S more, so a press just before or just
             after a bump both catch it. A recording with a jump in n, a time
             gap, an ok=0 row, a reset or a lost port is not saved, and the
             reason is given. Otherwise one press saves:
             - the raw 6 s to <session>/manual/<LABEL>-<n>.csv, the master
               copy for re-cutting later (M1), rows "n,t_us,ax,ay,az,gx,gy,gz,
               z_hp,roll,pitch,ok" exactly as the L lines sent them;
             - and at once, cut from that file by take_cut.cut_manual_file()
               (the cutter's own function), one finished 624-row take in the
               session folder, or in weak/ for a peak-aligned weak event
               (M2). Both files carry the same src_key, so cut_windows.py
               adds no duplicate later.
             ManualRecorder works in the GUI thread on the reader's sample
             batches. No Qt, so it is unit-tested.
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
from snapshot_protocol import L_FIELDS
from take_cut import CutResult, cut_manual_file, typical_peak_gap, window_problem
from take_store import TakeStore, header_text, iso_time

# ============================================================================
# Constants
# ============================================================================

# SaveOutcome.state values.
SAVED = "saved"          # Take in the session folder
WEAK = "weak"            # Take in weak/
NO_TAKE = "no_take"      # Raw recording kept, no take (nothing found, edge, no BOOT)
NOT_SAVED = "not_saved"  # Nothing written (gap, reset, ok=0, lost port, write error)

# ============================================================================
# Classes
# ============================================================================


@dataclass
class Capture:
    """One finished recording: the samples around a press."""
    label: str
    press_epoch_ms: int          # Laptop time of the press
    press_n: int                 # Latest sample count the GUI had at the press
    boot_raw: str                # The connection's BOOT line ("" if none seen)
    rows: list = field(default_factory=list)   # LogSample, oldest first
    rows_before: int = 0         # Rows that were already buffered at the press
    problem: str = ""            # Why it cannot be saved ("" = it can)

    @property
    def src_key(self) -> str:
        """@return The key shared by the raw recording and its take."""
        return "manual:%d:%d" % (self.press_epoch_ms, self.press_n)


@dataclass
class SaveOutcome:
    """What one press ended up saving."""
    state: str                          # SAVED, WEAK, NO_TAKE or NOT_SAVED
    message: str                        # For the status card and the log
    raw_path: Optional[Path] = None     # manual/<LABEL>-<n>.csv
    take_path: Optional[Path] = None    # The take (session folder or weak/)
    result: Optional[CutResult] = None  # The cut, with its alignment
    capture: Optional[Capture] = None


class ManualRecorder:
    """The rolling buffer and at most one recording at a time."""

    def __init__(self, pre_s: float = cfg.MANUAL_PRE_S, post_s: float = cfg.MANUAL_POST_S,
                 rate_hz: int = cfg.DEFAULT_RATE_HZ) -> None:
        """
        @param pre_s    Seconds kept before a press.
        @param post_s   Seconds collected after it.
        @param rate_hz  Sample rate (set_rate() once BOOT says otherwise).
        """
        self._pre_s = pre_s
        self._post_s = post_s
        self._buf: deque = deque()
        self._cur: Optional[Capture] = None
        self._post_left = 0
        self._last_n: Optional[int] = None
        self.set_rate(rate_hz)

    # ------------------------------------------------------------ properties

    @property
    def recording(self) -> bool:
        """@return True while a recording collects its samples after the press."""
        return self._cur is not None

    @property
    def label(self) -> str:
        """@return The label the current recording will be saved under ("" if none)."""
        return self._cur.label if self._cur is not None else ""

    @property
    def last_n(self) -> Optional[int]:
        """@return The latest sample count seen, or None."""
        return self._last_n

    @property
    def press_info(self) -> tuple:
        """@return (press_epoch_ms, press_n) of the current recording, or (None, None)."""
        if self._cur is None:
            return None, None
        return self._cur.press_epoch_ms, self._cur.press_n

    def remaining_s(self) -> float:
        """@return Seconds of samples still to collect (0 when not recording)."""
        return self._post_left / float(self._rate) if self._cur is not None else 0.0

    # ------------------------------------------------------------ user input

    def set_rate(self, rate_hz: int) -> None:
        """
        @param rate_hz  The board's sample rate (BOOT odr). Resizes the buffer.
        """
        self._rate = max(1, int(rate_hz))
        self._pre_n = int(round(self._pre_s * self._rate))
        self._post_n = int(round(self._post_s * self._rate))
        self._buf = deque(self._buf, maxlen=self._pre_n)

    def press(self, label: str, epoch_ms: int, boot_raw: Optional[str]) -> str:
        """
        Starts a recording (Record button, R, or P / B / F in the log build).
        @param label     Label to save it under.
        @param epoch_ms  Laptop time of the press, ms since the epoch.
        @param boot_raw  The connection's BOOT line, or None.
        @return "" if it started, else why not (presses during a recording
                are ignored).
        """
        if self._cur is not None:
            return "a recording is already running (%.1f s left)" % self.remaining_s()
        if self._last_n is None or not self._buf:
            return "no L samples yet (is the board running the log build?)"
        self._cur = Capture(label=label, press_epoch_ms=epoch_ms, press_n=self._last_n,
                            boot_raw=boot_raw or "", rows=list(self._buf),
                            rows_before=len(self._buf))
        self._post_left = self._post_n
        return ""

    def set_label(self, label: str) -> None:
        """
        @param label  New label for the recording being made (a key pressed
                      during its countdown).
        """
        if self._cur is not None:
            self._cur.label = label

    # ----------------------------------------------------------- device input

    def feed(self, samples: list) -> Optional[Capture]:
        """
        Adds a batch of L samples (oldest first).
        @param samples  LogSample list.
        @return The finished Capture if this batch completed (or broke) the
                recording, else None. Check its problem before saving.
        """
        done = None
        for s in samples:
            if self._last_n is not None and s.n <= self._last_n:
                # n went backwards: the board reset (its BOOT may be lost).
                self._buf.clear()
                if self._cur is not None and done is None:
                    done = self._finish("the board reset during the recording")
            self._last_n = s.n
            if self._cur is not None and done is None:
                self._cur.rows.append(s)
                self._post_left -= 1
                if self._post_left <= 0:
                    done = self._finish("")
            self._buf.append(s)
        return done

    def interrupt(self, reason: str) -> Optional[Capture]:
        """
        A BOOT line, a reconnect or a lost port: the buffer starts over, and
        a recording in progress cannot be saved.
        @param reason  Why, for the status line.
        @return The broken Capture if one was running, else None.
        """
        self._buf.clear()
        self._last_n = None
        if self._cur is None:
            return None
        return self._finish(reason)

    # ---------------------------------------------------------------- helpers

    def _finish(self, problem: str) -> Capture:
        """
        Ends the current recording.
        @param problem  Why it is broken ("" if it completed).
        @return The Capture, with its problem set if it cannot be saved.
        """
        cap = self._cur
        self._cur = None
        self._post_left = 0
        if not problem:
            bad = window_problem(cap.rows, 0, len(cap.rows))
            if bad:
                problem = "unusable data in the recording: %s" % bad
        cap.problem = problem
        return cap


def raw_recording_text(cap: Capture, session: str, gui_commit: str) -> str:
    """
    @param cap         A good Capture.
    @param session     Session name.
    @param gui_commit  Commit of this checkout.
    @return The raw recording file: "# key=value" header, then one row per
            sample, the L line's fields exactly as sent.
    """
    pairs = (("manual_format", cfg.MANUAL_FORMAT_VERSION),
             ("kind", "manual_recording"),
             ("label", cap.label),
             ("session", session),
             ("src_key", cap.src_key),
             ("gui_commit", gui_commit),
             ("press_pc", iso_time(cap.press_epoch_ms / 1000.0)),
             ("press_epoch_ms", cap.press_epoch_ms),
             ("press_n", cap.press_n),
             ("first_n", cap.rows[0].n),
             ("last_n", cap.rows[-1].n),
             ("rows_before", cap.rows_before),
             ("rows_after", len(cap.rows) - cap.rows_before),
             ("n_rows", len(cap.rows)),
             ("boot", cap.boot_raw or "missing (not seen on this connection)"),
             ("columns", ",".join(L_FIELDS)))
    return header_text(list(pairs)) + "".join(",".join(s.text) + "\n" for s in cap.rows)


def save_capture(store: TakeStore, session: str, cap: Capture, gui_commit: str) -> SaveOutcome:
    """
    Saves one press: the raw recording, then the take cut from it with the
    cutter's own function (request 8).
    @param store       Where takes go.
    @param session     Session name.
    @param cap         A finished Capture.
    @param gui_commit  Commit of this checkout.
    @return What was saved, with the message for the status card.
    """
    if cap.problem:
        return SaveOutcome(NOT_SAVED, "Not saved: %s" % cap.problem, capture=cap)
    try:
        raw_path = store.write_take(session, cap.label, raw_recording_text(cap, session, gui_commit),
                                    subdir=cfg.MANUAL_DIR_NAME)
    except (OSError, ValueError) as exc:
        return SaveOutcome(NOT_SAVED, "Not saved: %s" % exc, capture=cap)
    session_dir = store.session_dir(session)
    gap, _used = typical_peak_gap(session_dir)
    result = cut_manual_file(raw_path, session_dir, gap)
    if not result.ok:
        return SaveOutcome(NO_TAKE, result.message, raw_path=raw_path, result=result,
                           capture=cap)
    try:
        take_path = store.write_take(session, result.label, result.text, subdir=result.subdir)
    except (OSError, ValueError) as exc:
        return SaveOutcome(NO_TAKE, "Take not saved: %s" % exc, raw_path=raw_path,
                           result=result, capture=cap)
    return SaveOutcome(WEAK if result.subdir else SAVED, result.message, raw_path=raw_path,
                       take_path=take_path, result=result, capture=cap)
