"""
File:        live_plot.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Description: The "Live IMU" view: a scrolling plot of the S lines the board
             sends all the time (one sample every PRINT_EVERY_N, ~52 per
             second), so you can see the sensor working and watch a tap
             happen before its snapshot arrives. Accel (ax, ay, az and z_hp,
             g) on top, gyro (gx, gy, gz, dps) below, chip axes exactly as
             the S line sends them: the gyro still has its bias, which the
             snapshots have removed. Display only: it never records or saves
             anything (takes come from snapshots). Click a name in a legend
             to hide or show that curve.
"""

# ============================================================================
# Imports
# ============================================================================

import time
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import cnn_config as cfg
from app_styles import AppStyles
from live_buffer import LiveBuffer
from snapshot_plot import PLOT_BACKGROUND, axis_label_style, style_plot
from snapshot_protocol import S_CHANNELS, SampleLine

# ============================================================================
# Constants
# ============================================================================

# (S channel, legend name, pen) per plot. z_hp, gx and gy have the colors they
# have in the snapshot plots, so the two views are easy to compare.
_ACC_CURVES = (("ax", "ax", pg.mkPen((255, 140, 0), width=1)),
               ("ay", "ay", pg.mkPen("m", width=1)),
               ("az", "az", pg.mkPen("c", width=1)),
               ("z_hp", "z_hp", pg.mkPen("g", width=2)))
_GYR_CURVES = (("gx", "gx (roll)", pg.mkPen("b", width=1)),
               ("gy", "gy (pitch)", pg.mkPen("r", width=1)),
               ("gz", "gz (yaw)", pg.mkPen("y", width=1)))

# ============================================================================
# Classes
# ============================================================================


class LivePlot(QWidget):
    """Scrolling plot of the last LIVE_WINDOW_S seconds of S lines."""

    def __init__(self, title: str = "Live IMU") -> None:
        """
        @param title  Heading above the plots.
        """
        super().__init__()
        self.buffer = LiveBuffer(cfg.LIVE_WINDOW_S, cfg.LIVE_GAP_S)
        self._dirty = False                 # New samples since the last redraw
        self._connected = False
        self._last_wall: Optional[float] = None   # Monotonic time of the last sample

        layout = QVBoxLayout(self)

        header = QHBoxLayout()
        self.title = QLabel(title)
        self.title.setStyleSheet(AppStyles.HEADING_STYLE)
        header.addWidget(self.title)
        note = QLabel("S lines, chip axes, gyro bias not removed. Display only, never saved.")
        note.setStyleSheet(AppStyles.get_note_label_style())
        header.addWidget(note)
        header.addStretch(1)
        self.pause_check = QCheckBox("Pause")
        self.pause_check.setStyleSheet(AppStyles.get_label_style())
        self.pause_check.setToolTip("Freeze the plot. Lines keep arriving; untick to follow "
                                    "live again.")
        self.pause_check.toggled.connect(self._on_pause)
        header.addWidget(self.pause_check)
        layout.addLayout(header)

        self.view = pg.GraphicsLayoutWidget()
        self.view.setBackground(PLOT_BACKGROUND)
        layout.addWidget(self.view, 1)

        self.p_acc = self.view.addPlot(row=0, col=0)
        self.p_gyr = self.view.addPlot(row=1, col=0)
        self.p_gyr.setXLink(self.p_acc)  # Zoom and pan both together
        style_plot(self.p_acc, "accel (g)", legend_cols=len(_ACC_CURVES), x_values=False)
        style_plot(self.p_gyr, "gyro (dps)", legend_cols=len(_GYR_CURVES))
        self.p_gyr.getAxis("bottom").setLabel("time before the newest line (s)",
                                              **axis_label_style())
        # Fixed time axis (y stays on auto range); a mouse zoom is kept until
        # the "A" button in the plot's corner is clicked.
        self.p_acc.setXRange(-cfg.LIVE_WINDOW_S, 0, padding=0)

        self.curves = {}
        for plot, specs in ((self.p_acc, _ACC_CURVES), (self.p_gyr, _GYR_CURVES)):
            for channel, name, pen in specs:
                self.curves[channel] = plot.plot(pen=pen, name=name, connect="finite")

        self.info = QLabel("")
        self.info.setStyleSheet(AppStyles.get_status_label_style())
        self.info.setWordWrap(True)
        layout.addWidget(self.info)

        self.redraw_timer = QTimer(self)
        self.redraw_timer.timeout.connect(self._redraw)
        self.redraw_timer.start(cfg.LIVE_REDRAW_MS)
        self.readout_timer = QTimer(self)
        self.readout_timer.timeout.connect(self._readout)
        self.readout_timer.start(cfg.LIVE_READOUT_MS)
        self._readout()

    # ------------------------------------------------------------ public API

    def add_sample(self, s: SampleLine) -> None:
        """
        One S line from the reader (GUI thread, through a Qt signal).
        @param s  The sample.
        """
        self.buffer.add(s)
        self._last_wall = time.monotonic()
        self._dirty = True

    def reset(self) -> None:
        """Empties the plot (a new connection)."""
        self.buffer.clear()
        self._last_wall = None
        for curve in self.curves.values():
            curve.setData([], [])
        self._dirty = False
        self._readout()

    def set_connected(self, connected: bool) -> None:
        """
        @param connected  Whether the reader has the port (or the simulated
                          device) right now. The trace stays on screen either way.
        """
        self._connected = connected
        self._readout()

    def shutdown(self) -> None:
        """Stops the timers (window closing)."""
        self.redraw_timer.stop()
        self.readout_timer.stop()

    # ---------------------------------------------------------------- helpers

    def _on_pause(self, paused: bool) -> None:
        """
        @param paused  Pause checkbox state. Unpausing redraws at once.
        """
        if not paused:
            self._dirty = True
            self._redraw()
        self._readout()

    def _redraw(self) -> None:
        """Every LIVE_REDRAW_MS: redraws the curves if new lines came in."""
        if not self._dirty or self.pause_check.isChecked():
            return
        self._dirty = False
        t, cols = self.buffer.series()
        x = np.asarray(t, dtype=float)
        for channel, curve in self.curves.items():
            curve.setData(x, np.asarray(cols[channel], dtype=float), connect="finite")

    def _readout(self) -> None:
        """Every LIVE_READOUT_MS: the status text and the newest values."""
        s = self.buffer.latest()
        if self._connected and self._last_wall is not None:
            quiet = time.monotonic() - self._last_wall
            state = "NO S LINE FOR %.1f s" % quiet if quiet >= cfg.LIVE_STALE_S else None
        elif self._connected:
            state = "Connected: waiting for S lines"
        else:
            state = "Not connected"
        if self.pause_check.isChecked():
            state = "PAUSED (still receiving)" + ("" if state is None else ";  " + state)
        if s is None:
            self.info.setText(state or "")
            return
        rate = self.buffer.rate_hz()
        line1 = "%s lines/s  n %d  t %.3f s" % ("-" if rate is None else "%.1f" % rate,
                                                 s.n, s.t_us / 1e6)
        if state:
            line1 += "    " + state
        v = dict(zip(S_CHANNELS, s.values))
        line2 = ("ax %+.4f  ay %+.4f  az %+.4f  z_hp %+.4f g    "
                 "gx %+7.2f  gy %+7.2f  gz %+7.2f dps"
                 % (v["ax"], v["ay"], v["az"], v["z_hp"], v["gx"], v["gy"], v["gz"]))
        self.info.setText(line1 + "\n" + line2)
