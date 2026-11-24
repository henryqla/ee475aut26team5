"""
File:        live_inference_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.1.0
Change:      0.1.0 (training-live-inference-handoff.md, request G): new file.
Adapted from: Python/live_inference_widget.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             hero card (36 px headline, 13 px subline, green for a confident
             event, blue for the resting class, amber when unsure), one
             probability bar row per class (name, bar, percentage) with his
             bar style, the cutoff and exponential-moving-average smoothing
             controls (smoothing 0 to 0.85), Reset counters, and a stats line.
             Changed: no Connect, Start or commands (the tab shares the Record
             tab's connection, and the board streams on its own, so nothing is
             ever sent); the classes stay in model order and all are shown;
             the card and bars follow the board's detection rule (an event at
             or above the cutoff); WAITING FOR STREAM, NO DATA and MODEL FAULT
             states; a list of the board's own DET lines; a plot of the model
             channels with a marker at each DET; the shared serial monitor.
Description: The Live Inference tab (request G): what the CNN on the board is
             calling right now. It only reads PROB, DET, SELFTEST, BOOT / CFG
             (model fields), HLT (inference counters) and FLT (model faults)
             lines from the shared SerialHandler, and L samples for the plot.
             The display cutoff and smoothing change only what is shown (P6);
             the board decides its DET lines with its own DET_CONF. The record
             keys do nothing here (P7, set by the main window). InferenceView
             holds every display decision without Qt, so the unit tests check
             it directly.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import datetime
import html
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QCheckBox, QDoubleSpinBox, QFrame, QGroupBox, QHBoxLayout, QLabel,
                             QListWidget, QProgressBar, QPushButton, QScrollArea, QSplitter,
                             QVBoxLayout, QWidget)

import cnn_config as cfg
from app_styles import AppStyles, channel_pen, set_flag, set_role, set_state
from live_buffer import LiveBuffer
from snapshot_plot import axis_label_style, style_plot
from snapshot_protocol import DetLine, ProbLine, SampleLine, SelftestLine, model_classes

# ============================================================================
# Constants
# ============================================================================

_LOG = logging.getLogger("live")

# Card and bar states (app_styles tokens).
IDLE, OK, ARMED, WARN, ERROR = "idle", "ok", "armed", "warn", "error"

# The model channels the plot shows (L line z_hp, roll, pitch).
MODEL_CHANNELS = ("z_hp", "roll", "pitch")

# FLT codes of model faults (firmware types.h F5-F8): inference stopped.
MODEL_FAULT_CODES = ("F5", "F6", "F7", "F8")

NONE_LABEL = "NONE"

# PROB lines used for inferences per second (device time, from their n).
_RATE_PROBS = 10

# HLT fields of the inference counters.
_HLT_INFER = ("inferences", "inf_skips", "lat_last_us", "lat_max_us")

# ============================================================================
# Classes: display logic (no Qt)
# ============================================================================


@dataclass
class CardView:
    """What the status card shows."""
    state: str
    headline: str
    subline: str


class InferenceView:
    """Every display decision of the tab, from the lines received."""

    def __init__(self) -> None:
        self.classes = tuple(cfg.CLASSES)           # Model order (BOOT / CFG classes=)
        self.connected = False
        self.cutoff = cfg.LIVE_INF_DEFAULT_CUTOFF   # Display only (P6)
        self.cutoff_from_board = False              # Taken from det_conf on this connection
        self.board_cutoff: Optional[float] = None   # The board's DET_CONF
        self.smooth = cfg.LIVE_INF_SMOOTH_DEFAULT   # EMA factor, 0 = off (G1)
        self.shown: Optional[np.ndarray] = None     # Probabilities as shown (smoothed)
        self.last_wall: Optional[float] = None      # Monotonic time of the last PROB
        self.recent = deque(maxlen=cfg.LIVE_INF_RECENT_N)   # (clock, label, conf, n), newest first
        self.model: Optional[str] = None
        self.selftest: Optional[str] = None
        self.selftest_diff: Optional[float] = None
        self.model_fault: Optional[tuple] = None    # (code, name) of an FLT F5-F8
        self.board = {}                             # HLT inference counters
        self.count = 0
        self.lat_last_us: Optional[int] = None
        self.lat_max_us: Optional[int] = None
        self._rate_ns = deque(maxlen=_RATE_PROBS)

    def reset_counters(self) -> None:
        """Reset counters: PROB count, rate, times and the recent detections."""
        self.count = 0
        self.lat_last_us = None
        self.lat_max_us = None
        self._rate_ns.clear()
        self.recent.clear()

    def new_connection(self) -> None:
        """A new connection, or the board came back: its model facts start over."""
        self.cutoff_from_board = False
        self.shown = None
        self.last_wall = None
        self.model = None
        self.selftest = None
        self.selftest_diff = None
        self.model_fault = None
        self.board = {}
        self._rate_ns.clear()

    def apply_settings(self, settings: dict) -> Optional[float]:
        """
        BOOT or CFG settings: the model's classes, version, self-test and the
        board's det_conf.
        @param settings  key -> text.
        @return The board's det_conf if it just set the display cutoff (the
                first time on this connection), else None.
        """
        classes = model_classes(settings)
        if classes:
            self.classes = classes
        self.model = settings.get("model", self.model)
        if settings.get("selftest"):
            self.selftest = settings["selftest"]
        try:
            self.selftest_diff = float(settings["selftest_diff"])
        except (KeyError, ValueError):
            pass
        try:
            conf = float(settings["det_conf"])
        except (KeyError, ValueError):
            return None
        self.board_cutoff = conf
        if self.cutoff_from_board:
            return None
        self.cutoff_from_board = True
        self.cutoff = conf
        return conf

    def add_prob(self, prob: ProbLine, wall: float) -> None:
        """
        One PROB line: smoothing (shown = a x shown + (1 - a) x new), counters.
        @param prob  The line.
        @param wall  Monotonic time it arrived.
        """
        p = np.clip(np.asarray(prob.probs, dtype=float), 0.0, 1.0)
        if self.smooth > 0.0 and self.shown is not None and len(self.shown) == len(p):
            self.shown = self.smooth * self.shown + (1.0 - self.smooth) * p
        else:
            self.shown = p
        self.last_wall = wall
        self.count += 1
        self.lat_last_us = prob.lat_us
        self.lat_max_us = prob.lat_us if self.lat_max_us is None else max(self.lat_max_us,
                                                                             prob.lat_us)
        self._rate_ns.append(prob.n)

    def add_det(self, det: DetLine, clock: str) -> None:
        """
        @param det    The board's DET line.
        @param clock  Time of day it arrived, HH:MM:SS.
        """
        self.recent.appendleft((clock, det.label, det.conf, det.n))

    def rate(self, rate_hz: float) -> Optional[float]:
        """@return Inferences per second of device time over the last PROB lines, or None."""
        ns = self._rate_ns
        if len(ns) < 2 or ns[-1] <= ns[0]:
            return None
        return (len(ns) - 1) * rate_hz / (ns[-1] - ns[0])

    def name(self, k: int) -> str:
        """@return Class k's name (a placeholder if the model sent more values)."""
        return self.classes[k] if k < len(self.classes) else "class %d" % k

    def state_of(self, k: int, p: float) -> str:
        """
        The board's rule, for the display: an event at or above the cutoff is
        green, NONE blue, an event below the cutoff amber (unsure).
        @param k  Class index.
        @param p  Its shown probability.
        @return OK, ARMED or WARN.
        """
        if self.name(k) == NONE_LABEL:
            return ARMED
        return OK if p >= self.cutoff else WARN

    def card(self, now: float) -> CardView:
        """
        @param now  Monotonic time.
        @return The status card's state, headline and subline.
        """
        if not self.connected:
            return CardView(IDLE, "NOT CONNECTED", "Connect on the Record tab (a port or the "
                                                   "simulated device)")
        if self.model_fault is not None:
            return CardView(ERROR, "MODEL FAULT %s" % self.model_fault[0],
                            "%s: the board's inference stopped. Streaming and recording go "
                            "on" % self.model_fault[1])
        if self.last_wall is None or self.shown is None:
            return CardView(IDLE, "WAITING FOR STREAM", "No PROB line yet. The board starts "
                                                        "once it has 2 s of settled readings")
        quiet = max(0.0, now - self.last_wall)
        if quiet >= cfg.LIVE_INF_STALE_S:
            return CardView(IDLE, "NO DATA", "No PROB line for %.1f s" % quiet)
        k = int(np.argmax(self.shown))              # First maximum, as the board
        p = float(self.shown[k])
        state = self.state_of(k, p)
        if state == WARN:
            sub = "unsure: %.0f%%, below the display cutoff %.0f%%" % (100 * p, 100 * self.cutoff)
        else:
            sub = "%.0f%%" % (100 * p)
        return CardView(state, self.name(k), "%s · updated %.1f s ago" % (sub, quiet))

    def bars(self) -> list:
        """@return (name, shown probability, state) per class, in model order."""
        if self.shown is None:
            return [(c, 0.0, IDLE) for c in self.classes]
        return [(self.name(k), float(p), self.state_of(k, float(p)))
                for k, p in enumerate(self.shown)]

    def stats_html(self, malformed: int, rate_hz: float) -> str:
        """
        @param malformed  Malformed lines since the last Reset counters.
        @param rate_hz    Sample rate (for inferences per second).
        @return The stats line, rich text; a failed self-test in red.
        """
        rate = self.rate(rate_hz)
        ms = (lambda us: "-" if us is None else "%.1f" % (us / 1000.0))
        parts = ["inferences %s (received %d)" % (self.board.get("inferences", "-"), self.count),
                 "%s/s" % ("-" if rate is None else "%.2f" % rate),
                 "last %s ms" % ms(self.lat_last_us),
                 "max %s ms" % ms(self.lat_max_us),
                 "skips %s" % self.board.get("inf_skips", "-"),
                 "malformed %d" % malformed,
                 "model %s" % html.escape(self.model or "-")]
        st = self.selftest or "-"
        text = "self-test %s" % html.escape(st)
        if self.selftest_diff is not None:
            text += " (diff %.2g)" % self.selftest_diff
        if st in ("fail", "not_run"):
            text = '<span style="color:%s">%s</span>' % (AppStyles.ACCENT_ERROR, text)
        parts.append(text)
        return " · ".join(parts)

# ============================================================================
# Classes: widgets
# ============================================================================


class _ProbRow(QWidget):
    """One class: name, bar, percentage (Jake's _ProbabilityBarRow, no rank)."""

    def __init__(self) -> None:
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(2, 2, 2, 2)
        lay.setSpacing(8)
        self.name = QLabel("-")
        self.name.setFixedWidth(110)
        self.bar = set_role(QProgressBar(), "prob")
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.pct = set_role(QLabel("0.0%"), "mono")
        self.pct.setFixedWidth(64)
        self.pct.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        lay.addWidget(self.name)
        lay.addWidget(self.bar, 1)
        lay.addWidget(self.pct)

    def show_value(self, name: str, p: float, state: str) -> None:
        """
        @param name   Class name.
        @param p      Shown probability, 0..1.
        @param state  OK, ARMED, WARN or IDLE: the bar's color.
        """
        p = max(0.0, min(1.0, p))
        self.name.setText(name)
        self.bar.setValue(int(round(p * 1000)))
        self.pct.setText("%5.1f%%" % (100 * p))
        set_state(self.bar, state)


class ModelPlot(QGroupBox):
    """The last LIVE_WINDOW_S of z_hp, roll and pitch, with a marker at each DET."""

    def __init__(self, title: str = "Model channels (what the board's model sees)") -> None:
        super().__init__(title)
        self.buffer = LiveBuffer(cfg.LIVE_WINDOW_S, cfg.LIVE_GAP_S, channels=MODEL_CHANNELS)
        self.rate_hz = float(cfg.DEFAULT_RATE_HZ)
        self._dets = deque(maxlen=64)       # (n, label) of recent DET lines
        self._markers = []                  # InfiniteLines now drawn
        self._dirty = False

        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)
        self.view = pg.GraphicsLayoutWidget()
        self.view.setBackground(AppStyles.PLOT_BG)
        layout.addWidget(self.view, 1)
        self.p_z = self.view.addPlot(row=0, col=0)
        self.p_rp = self.view.addPlot(row=1, col=0)
        self.p_rp.setXLink(self.p_z)
        z_legend = style_plot(self.p_z, "z_hp (g)", legend_cols=1, x_values=False)
        rp_legend = style_plot(self.p_rp, "roll, pitch (dps)", legend_cols=2)
        self.p_rp.getAxis("bottom").setLabel("time before the newest sample (s)",
                                             **axis_label_style())
        self.p_z.setXRange(-cfg.LIVE_WINDOW_S, 0, padding=0)
        self.curves = {}
        for plot, legend, channel, token in ((self.p_z, z_legend, "z_hp", "PEN_Z_HP"),
                                             (self.p_rp, rp_legend, "roll", "PEN_ROLL"),
                                             (self.p_rp, rp_legend, "pitch", "PEN_PITCH")):
            self.curves[channel] = plot.plot(pen=channel_pen(getattr(AppStyles, token), True),
                                             connect="finite")
            legend.addItem(self.curves[channel], channel)
        note = set_role(QLabel(
            "Every %dth L sample, bias removed, as the model gets it. A dashed green line "
            "marks each DET the board sent, at the newest sample of the window that fired. "
            "Display only." % cfg.LIVE_DECIMATE), "note")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._redraw)
        self.timer.start(cfg.LIVE_REDRAW_MS)

    def add_sample(self, s: SampleLine) -> None:
        """@param s  One point: values are z_hp, roll, pitch."""
        if self.buffer.add(s):
            self._dets.clear()              # Device time went back: the board reset
        self._dirty = True

    def add_det(self, n: int, label: str) -> None:
        """@param n  The DET's sample count.  @param label  Its class."""
        self._dets.append((n, label))
        self._dirty = True

    def reset(self) -> None:
        """Forgets every point and marker (new connection)."""
        self.buffer.clear()
        self._dets.clear()
        self._dirty = True

    def shutdown(self) -> None:
        """Stops the redraw timer."""
        self.timer.stop()

    def _redraw(self) -> None:
        """Every LIVE_REDRAW_MS while visible: curves and DET markers."""
        if not self._dirty or not self.isVisible():
            return
        self._dirty = False
        t, cols = self.buffer.series()
        x = np.asarray(t, dtype=float)
        for channel, curve in self.curves.items():
            curve.setData(x, np.asarray(cols[channel], dtype=float), connect="finite")
        for plot, line in self._markers:
            plot.removeItem(line)
        self._markers = []
        latest = self.buffer.latest()
        if latest is None:
            return
        pen = pg.mkPen(AppStyles.ACCENT_OK, width=2, style=Qt.DashLine)
        for n, label in self._dets:
            pos = (n - latest.n) / self.rate_hz
            if not -cfg.LIVE_WINDOW_S <= pos <= 0.0:
                continue
            for plot, text in ((self.p_z, label), (self.p_rp, None)):
                line = pg.InfiniteLine(pos=pos, angle=90, pen=pen, movable=False, label=text,
                                       labelOpts={"position": 0.9, "color": AppStyles.ACCENT_OK})
                plot.addItem(line)
                self._markers.append((plot, line))


class LiveInferenceWidget(QWidget):
    """The Live Inference tab."""

    def __init__(self) -> None:
        super().__init__()
        self.view = InferenceView()
        self.handler = None                 # The shared SerialHandler (attach)
        self._malformed_base = 0            # Malformed lines at the last Reset counters

        # ---- Left column: card, bars and controls, recent detections, stats.
        card_box = QGroupBox("Prediction")
        card_layout = QVBoxLayout(card_box)
        self.hero = set_role(QFrame(), "card")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(12, 8, 12, 10)
        hero_layout.setSpacing(2)
        self.headline = set_role(QLabel(""), "card-headline")
        set_flag(self.headline, "fit", 0)   # 36 px (CARD_HEADLINE_FONT_SIZES[0])
        self.headline.setAlignment(Qt.AlignCenter)
        self.subline = set_role(QLabel(""), "card-subline")
        self.subline.setAlignment(Qt.AlignCenter)
        self.subline.setWordWrap(True)
        hero_layout.addWidget(self.headline)
        hero_layout.addWidget(self.subline)
        card_layout.addWidget(self.hero)

        bars_box = QGroupBox("Probabilities")
        bars_layout = QVBoxLayout(bars_box)
        self.rows = []
        for _ in cfg.CLASSES:
            row = _ProbRow()
            self.rows.append(row)
            bars_layout.addWidget(row)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Display cutoff"))
        self.cutoff_spin = QDoubleSpinBox()
        self.cutoff_spin.setRange(0.05, 1.0)
        self.cutoff_spin.setSingleStep(0.05)
        self.cutoff_spin.setDecimals(2)
        self.cutoff_spin.setValue(self.view.cutoff)
        self.cutoff_spin.setToolTip("Display only: green from this probability. It starts at "
                                    "the board's DET_CONF; the board's DET lines use its own")
        self.cutoff_spin.valueChanged.connect(self._on_cutoff)
        controls.addWidget(self.cutoff_spin)
        controls.addSpacing(12)
        controls.addWidget(QLabel("Smoothing"))
        self.smooth_spin = QDoubleSpinBox()
        self.smooth_spin.setRange(0.0, cfg.LIVE_INF_SMOOTH_MAX)
        self.smooth_spin.setSingleStep(0.05)
        self.smooth_spin.setDecimals(2)
        self.smooth_spin.setValue(self.view.smooth)
        self.smooth_spin.setToolTip("Display only: exponential moving average, 0 = off")
        self.smooth_spin.valueChanged.connect(self._on_smooth)
        controls.addWidget(self.smooth_spin)
        controls.addStretch(1)
        bars_layout.addLayout(controls)
        note = set_role(QLabel("These controls change only this display. The board decides "
                               "its own detections (Recent detections) with DET_CONF in "
                               "config.h."), "note")
        note.setWordWrap(True)
        bars_layout.addWidget(note)

        recent_box = QGroupBox("Recent detections (the board's DET lines)")
        recent_layout = QVBoxLayout(recent_box)
        self.recent_list = set_role(QListWidget(), "mono")
        self.recent_list.setMinimumHeight(120)
        recent_layout.addWidget(self.recent_list)

        self.stats = set_role(QLabel(""), "info")
        self.stats.setWordWrap(True)
        self.stats.setTextFormat(Qt.RichText)
        self.reset_button = QPushButton("Reset counters")
        self.reset_button.setToolTip("Display only: the counts, rate, times and the recent "
                                     "detections start over")
        self.reset_button.clicked.connect(self._on_reset)
        stats_row = QHBoxLayout()
        stats_row.addWidget(self.stats, 1)
        stats_row.addWidget(self.reset_button, 0, Qt.AlignTop)

        self.sim_box = QGroupBox("Simulated device (test controls)")
        sim_layout = QHBoxLayout(self.sim_box)
        self.prob_check = QCheckBox("PROB lines")
        self.prob_check.setChecked(True)
        self.prob_check.setToolTip("Untick to stop the simulated board's PROB lines (NO DATA "
                                   "after 2 s) while L lines go on")
        self.prob_check.toggled.connect(lambda on: self._sim("prob", on))
        sim_layout.addWidget(self.prob_check)
        for text, shape in (("Inject pothole", "POTHOLE"), ("Inject speed bump", "SPEED_BUMP")):
            button = QPushButton(text)
            button.clicked.connect(lambda _=False, s=shape: self._sim("event", s, 0.5))
            sim_layout.addWidget(button)
        bad = QPushButton("Send malformed lines")
        bad.clicked.connect(lambda _=False: self._sim("malformed"))
        sim_layout.addWidget(bad)
        sim_layout.addStretch(1)
        self.sim_box.hide()

        left = set_role(QWidget(), "column")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(AppStyles.WIDGET_SPACING)
        for w in (card_box, bars_box, recent_box):
            left_layout.addWidget(w)
        left_layout.addLayout(stats_row)
        left_layout.addWidget(self.sim_box)
        left_layout.addStretch(1)
        left_scroll = set_role(QScrollArea(), "column")
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(AppStyles.LEFT_COLUMN_MIN_WIDTH)

        # ---- Right column: the plot; the main window puts the shared serial
        # monitor under it while this tab is open.
        self.plot = ModelPlot()
        self.plot.setMinimumHeight(AppStyles.PLOT_MIN_HEIGHT)
        self.right = QSplitter(Qt.Vertical)
        self.right.setChildrenCollapsible(False)
        self.right.setHandleWidth(AppStyles.WIDGET_SPACING)
        self.right.addWidget(self.plot)

        columns = QSplitter(Qt.Horizontal)
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(AppStyles.WIDGET_SPACING)
        columns.addWidget(left_scroll)
        columns.addWidget(self.right)
        columns.setStretchFactor(0, 1)
        columns.setStretchFactor(1, 2)
        columns.setSizes([1000, 2000])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 8, 12, 12)
        outer.addWidget(columns, 1)

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._refresh)
        self.timer.start(cfg.LIVE_INF_REFRESH_MS)
        self._refresh()

    # ------------------------------------------------------------ public API

    def attach(self, handler) -> None:
        """
        Listens to a new SerialHandler (RecordWidget.handler_started), before
        it starts. Never sends anything to the board.
        @param handler  The SerialHandler.
        """
        self.handler = handler
        handler.boot_received.connect(self._on_settings)
        handler.config_received.connect(self._on_settings)
        handler.samples_received.connect(self._on_samples)
        handler.prob_received.connect(self._on_probs)
        handler.det_received.connect(self._on_det)
        handler.selftest_received.connect(self._on_selftest)
        handler.health_received.connect(self._on_health)
        handler.fault_received.connect(self._on_fault)
        handler.connection_changed.connect(self._on_connection)
        handler.reconnected.connect(self._on_reconnected)
        self.view.new_connection()
        self.plot.reset()
        self._malformed_base = 0
        self.sim_box.setVisible(handler.simulated)
        self.prob_check.setChecked(True)
        self._refresh()

    def shutdown(self) -> None:
        """Stops the timers (window closing)."""
        self.timer.stop()
        self.plot.shutdown()

    # ---------------------------------------------------------------- slots

    def _on_settings(self, line) -> None:
        """@param line  A BootLine or ConfigLine: model fields, det_conf, rate."""
        conf = self.view.apply_settings(line.settings)
        try:
            self.plot.rate_hz = float(line.settings.get("odr", cfg.DEFAULT_RATE_HZ))
        except ValueError:
            pass
        if conf is not None:
            self.cutoff_spin.blockSignals(True)
            self.cutoff_spin.setValue(conf)
            self.cutoff_spin.blockSignals(False)
            _LOG.info("Model %s, classes %s, board DET_CONF %.2f, self-test %s",
                      self.view.model or "-", "|".join(self.view.classes), conf,
                      self.view.selftest or "-")
        self._refresh()

    def _on_samples(self, batch: list) -> None:
        """@param batch  LogSamples: every LIVE_DECIMATE-th goes to the plot."""
        for s in batch:
            if s.n % cfg.LIVE_DECIMATE == 0:
                self.plot.add_sample(SampleLine(s.n, s.t_us, s.model))

    def _on_probs(self, batch: list) -> None:
        """@param batch  ProbLines, oldest first."""
        now = time.monotonic()
        for prob in batch:
            self.view.add_prob(prob, now)
        self._refresh()

    def _on_det(self, det: DetLine) -> None:
        """@param det  The board's detection."""
        self.view.add_det(det, datetime.datetime.now().strftime("%H:%M:%S"))
        self.plot.add_det(det.n, det.label)
        _LOG.info("DET %s %.2f (n %d)", det.label, det.conf, det.n)
        self._refresh()

    def _on_selftest(self, line: SelftestLine) -> None:
        """@param line  The boot self-test result."""
        self.view.selftest = line.result
        self.view.selftest_diff = line.max_diff
        log = _LOG.info if line.result == "pass" else _LOG.warning
        log("SELFTEST %s (largest difference %.2g)", line.result, line.max_diff)
        self._refresh()

    def _on_health(self, hlt) -> None:
        """@param hlt  A HealthLine: the board's inference counters (0.1.5-bench)."""
        self.view.board = {k: hlt.fields[k] for k in _HLT_INFER if k in hlt.fields}

    def _on_fault(self, flt) -> None:
        """@param flt  A FaultLine: F5-F8 means the board's inference stopped."""
        if flt.code in MODEL_FAULT_CODES:
            self.view.model_fault = (flt.code, flt.name)
            self._refresh()

    def _on_connection(self, ok: bool, _message: str) -> None:
        """@param ok  Connected?"""
        self.view.connected = ok
        self._refresh()

    def _on_reconnected(self) -> None:
        """The port came back: the board may have reset or been reflashed."""
        self.view.new_connection()
        self.plot.reset()
        self._refresh()

    def _on_cutoff(self, value: float) -> None:
        """@param value  New display cutoff (display only, P6)."""
        self.view.cutoff = float(value)
        self._refresh()

    def _on_smooth(self, value: float) -> None:
        """@param value  New smoothing factor (display only, P6)."""
        self.view.smooth = float(value)

    def _on_reset(self) -> None:
        """Reset counters (display only)."""
        self.view.reset_counters()
        self._malformed_base = self._malformed_total()
        self._refresh()

    def _sim(self, *cmd) -> None:
        """A simulated-device test control (ignored for a real port)."""
        if self.handler is not None:
            self.handler.sim_command(*cmd)

    # ---------------------------------------------------------------- helpers

    def _malformed_total(self) -> int:
        """@return Malformed lines this connection (every counted tag)."""
        if self.handler is None:
            return 0
        return sum(self.handler.malformed.values())

    def _refresh(self) -> None:
        """Card, bars, recent detections and stats from the view."""
        card = self.view.card(time.monotonic())
        self.headline.setText(card.headline)
        self.subline.setText(card.subline)
        for w in (self.hero, self.headline):
            set_state(w, card.state)
        for row, (name, p, state) in zip(self.rows, self.view.bars()):
            row.show_value(name, p, state)
        items = ["%s  %-11s %.2f   n %d" % r for r in self.view.recent]
        if [self.recent_list.item(i).text() for i in range(self.recent_list.count())] != items:
            self.recent_list.clear()
            self.recent_list.addItems(items)
        malformed = max(0, self._malformed_total() - self._malformed_base)
        self.stats.setText(self.view.stats_html(malformed, self.plot.rate_hz))
