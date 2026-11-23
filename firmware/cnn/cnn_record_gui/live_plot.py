"""
File:        live_plot.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.2.0
Adapted from: (layout only, request 7) the "Real-Time IMU Acceleration"
             panel in Python/live_inference_widget.py in
             ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             line of newest values above the plot with a control on its
             right.
Description: The "Live IMU" view: a scrolling plot of the S lines the board
             sends all the time (one sample every PRINT_EVERY_N, ~52 per
             second), so you can see the sensor working and watch a tap
             happen before its snapshot arrives. Accel (ax, ay, az and z_hp,
             g) on top, gyro (gx, gy, gz, dps) below, chip axes exactly as
             the S line sends them: the gyro still has its bias, which the
             snapshots have removed. Display only: it never records or saves
             anything (takes come from snapshots). Click a name in a legend
             to hide or show that curve. Restyled (request 7): a panel with
             a one-line readout above the plot (all seven values in its
             tooltip), a state tag and Pause beside it; each sub-plot's name
             and legend in a row above it; model channels thick, raw ones
             thin and muted; colors from app_styles.py.
"""

# ============================================================================
# Imports
# ============================================================================

import time
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import QTimer
from PyQt5.QtWidgets import QCheckBox, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout

import cnn_config as cfg
from app_styles import AppStyles, channel_pen, set_role, set_state
from elided_label import ElidedLabel
from live_buffer import LiveBuffer
from snapshot_plot import axis_label_style, style_plot
from snapshot_protocol import S_CHANNELS, SampleLine

# ============================================================================
# Constants
# ============================================================================

# (S channel, legend name, AppStyles pen token, model channel?) per plot.
# z_hp, gx and gy are the model channels and have the colors they have in
# the snapshot plots, so the two views are easy to compare.
_ACC_CURVES = (("ax", "ax", "PEN_AX", False),
               ("ay", "ay", "PEN_AY", False),
               ("az", "az", "PEN_AZ", False),
               ("z_hp", "z_hp", "PEN_Z_HP", True))
_GYR_CURVES = (("gx", "gx (roll)", "PEN_ROLL", True),
               ("gy", "gy (pitch)", "PEN_PITCH", True),
               ("gz", "gz (yaw)", "PEN_GZ", False))

# ============================================================================
# Classes
# ============================================================================


class LivePlot(QGroupBox):
    """Scrolling plot of the last LIVE_WINDOW_S seconds of S lines."""

    def __init__(self, title: str = "Live IMU") -> None:
        """
        @param title  Panel heading.
        """
        super().__init__(title)
        self.buffer = LiveBuffer(cfg.LIVE_WINDOW_S, cfg.LIVE_GAP_S)
        self._dirty = False                 # New samples since the last redraw
        self._connected = False
        self._last_wall: Optional[float] = None   # Monotonic time of the last sample

        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)

        # Header: newest values, then the state tag and Pause on the right.
        header = QHBoxLayout()
        self.info = set_role(ElidedLabel(""), "readout")
        header.addWidget(self.info, 1)
        self.state_tag = set_role(QLabel(""), "tag")
        self.state_tag.hide()
        header.addWidget(self.state_tag)
        self.pause_check = QCheckBox("Pause")
        self.pause_check.setToolTip("Freeze the plot. Lines keep arriving; untick to follow "
                                    "live again.")
        self.pause_check.toggled.connect(self._on_pause)
        header.addWidget(self.pause_check)
        layout.addLayout(header)

        self.view = pg.GraphicsLayoutWidget()
        self.view.setBackground(AppStyles.PLOT_BG)
        layout.addWidget(self.view, 1)

        self.p_acc = self.view.addPlot(row=0, col=0)
        self.p_gyr = self.view.addPlot(row=1, col=0)
        self.p_gyr.setXLink(self.p_acc)  # Zoom and pan both together
        acc_legend = style_plot(self.p_acc, "accel (g)", legend_cols=len(_ACC_CURVES),
                                x_values=False)
        gyr_legend = style_plot(self.p_gyr, "gyro (dps)", legend_cols=len(_GYR_CURVES))
        self.p_gyr.getAxis("bottom").setLabel("time before the newest line (s)",
                                              **axis_label_style())
        # Fixed time axis (y stays on auto range); a mouse zoom is kept until
        # the "A" button in the plot's corner is clicked.
        self.p_acc.setXRange(-cfg.LIVE_WINDOW_S, 0, padding=0)

        self.curves = {}
        for plot, legend, specs in ((self.p_acc, acc_legend, _ACC_CURVES),
                                    (self.p_gyr, gyr_legend, _GYR_CURVES)):
            for channel, name, token, model in specs:
                pen = channel_pen(getattr(AppStyles, token), model)
                self.curves[channel] = plot.plot(pen=pen, connect="finite")
                legend.addItem(self.curves[channel], name)

        note = set_role(QLabel("S lines, chip axes, gyro bias not removed. Display only, "
                               "never saved."), "note")
        layout.addWidget(note)

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

    def lines_per_second(self) -> Optional[float]:
        """
        @return S lines per second over the window, or None (too few yet).
        """
        return self.buffer.rate_hz()

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
        """Every LIVE_READOUT_MS: the state tag and the newest values."""
        s = self.buffer.latest()
        stale = False
        if self._connected and self._last_wall is not None:
            quiet = time.monotonic() - self._last_wall
            stale = quiet >= cfg.LIVE_STALE_S
            state = "NO S LINE FOR %.1f s" % quiet if stale else None
        elif self._connected:
            state = "Connected: waiting for S lines"
        else:
            state = "Not connected"
        paused = self.pause_check.isChecked()
        if paused:
            state = "PAUSED (still receiving)" + ("" if state is None else ";  " + state)
        self.state_tag.setVisible(state is not None)
        if state is not None:
            self.state_tag.setText(state)
            set_state(self.state_tag, "warn" if stale else "armed" if paused else "idle")
        if s is None:
            self.info.setText("")
            self.info.set_detail("")
            return
        rate = self.buffer.rate_hz()
        rate_text = "-" if rate is None else "%.1f" % rate
        v = dict(zip(S_CHANNELS, s.values))
        self.info.setText("z_hp %+.4f g   roll %+7.2f  pitch %+7.2f dps   %s lines/s   n %d"
                          % (v["z_hp"], v["gx"], v["gy"], rate_text, s.n))
        self.info.set_detail(
            "%s lines/s  n %d  t %.3f s\n"
            "ax %+.4f  ay %+.4f  az %+.4f  z_hp %+.4f g\n"
            "gx %+7.2f  gy %+7.2f  gz %+7.2f dps"
            % (rate_text, s.n, s.t_us / 1e6, v["ax"], v["ay"], v["az"], v["z_hp"],
               v["gx"], v["gy"], v["gz"]))
