"""
File:        live_buffer.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.1
Change:      0.1.1 (manual-recording-overhaul-handoff.md, request B): comments
             only. The points are every LIVE_DECIMATE-th L sample now that the
             firmware sends no S lines.
Description: The last few seconds of live points (every LIVE_DECIMATE-th L
             sample, as SampleLine), for the live IMU view. Keeps samples by device
             time, starts over when device time goes backwards (a reset or a
             different board), and gives the plot its series with a break
             (NaN) wherever lines are missing. Display only: nothing here is
             ever saved. Standard library only, so it runs in the unit tests.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import math
from collections import deque
from typing import Optional

from snapshot_protocol import S_CHANNELS, SampleLine

# ============================================================================
# Classes
# ============================================================================


class LiveBuffer:
    """Live points from the last window_s seconds of device time."""

    def __init__(self, window_s: float, gap_s: float, max_points: int = 20000) -> None:
        """
        @param window_s    Seconds of device time kept.
        @param gap_s       Samples further apart than this get a break between them.
        @param max_points  Hard limit on samples kept, whatever their times.
        """
        self._window_us = int(window_s * 1e6)
        self._gap_us = int(gap_s * 1e6)
        self._rows: deque = deque(maxlen=max_points)
        self.restarts = 0     # Times device time went backwards since clear()

    def clear(self) -> None:
        """Forgets every sample (new connection)."""
        self._rows.clear()
        self.restarts = 0

    def add(self, s: SampleLine) -> bool:
        """
        Adds one sample and drops those older than the window.
        @param s  The sample.
        @return True if the buffer started over first, because device time did
                not move forward (the board reset, or another board).
        """
        restarted = False
        if self._rows and s.t_us <= self._rows[-1].t_us:
            self._rows.clear()
            self.restarts += 1
            restarted = True
        self._rows.append(s)
        cutoff = s.t_us - self._window_us
        while self._rows[0].t_us < cutoff:
            self._rows.popleft()
        return restarted

    def __len__(self) -> int:
        """
        @return Samples in the window.
        """
        return len(self._rows)

    def latest(self) -> Optional[SampleLine]:
        """
        @return The newest sample, or None if empty.
        """
        return self._rows[-1] if self._rows else None

    def rate_hz(self) -> Optional[float]:
        """
        @return Points per second of device time over the last second, or
                None with fewer than two samples in it.
        """
        if len(self._rows) < 2:
            return None
        t_last = self._rows[-1].t_us
        k, t_first = 0, t_last
        for s in reversed(self._rows):
            if t_last - s.t_us > 1_000_000:
                break
            k += 1
            t_first = s.t_us
        if k < 2 or t_last == t_first:
            return None
        return (k - 1) * 1e6 / (t_last - t_first)

    def series(self) -> tuple:
        """
        The window as plot data. Times are seconds relative to the newest
        sample (so 0 is "now" and older samples are negative). Where two
        neighbours are more than gap_s apart, one NaN point is put between
        them, so a plot that skips non-finite points shows a break.
        @return (t, channels): t is a list of floats; channels maps each
                S_CHANNELS name to a list of floats of the same length.
        """
        t, cols = [], {name: [] for name in S_CHANNELS}
        if not self._rows:
            return t, cols
        t_last = self._rows[-1].t_us
        prev = None
        for s in self._rows:
            if prev is not None and s.t_us - prev > self._gap_us:
                t.append(math.nan)
                for name in S_CHANNELS:
                    cols[name].append(math.nan)
            t.append((s.t_us - t_last) / 1e6)
            for name, v in zip(S_CHANNELS, s.values):
                cols[name].append(v)
            prev = s.t_us
        return t, cols
