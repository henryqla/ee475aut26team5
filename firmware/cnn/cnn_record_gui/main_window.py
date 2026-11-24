"""
File:        main_window.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.4.0
Change:      0.4.0 (manual-recording-overhaul-handoff.md, request B): manual
             clips. A top bar across the Record tab shows the session's clip
             counts per label and the long NONE minutes. The Last snapshot and
             Selected take plots become one clip plot with an info panel of
             plain facts beside it. The Training tab is gone: the owner
             retired the request-3 training code (git tag
             pre-manual-overhaul); training on sliding windows comes in a
             later handoff.
             0.3.0: Training tab (request 3).
Adapted from: Python/main_window.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             window, the Playground tab's layout (controls and clips in a
             left column, a big live plot top right, the clip plot below it)
             and its spacing. Changed: one tab, Record; the Codegen and Live
             Inference tabs and the inference widget are removed; a live IMU
             view, a status card, a clip info panel, a top bar and a serial
             monitor are added, and the parts can be resized with splitters.
Description: Main window of the CNN record GUI. Record tab: the top bar, then
             on the left the Record panel, the Last clip card and Collected
             clips, the full window height (the column scrolls if the window
             is too short); on the right, Live IMU on top, the clip plot and
             its info panel below it, and the serial monitor at the bottom.
             Drag the gaps between them to resize.
"""

# ============================================================================
# Imports
# ============================================================================

import logging

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QFrame, QLabel, QMainWindow, QScrollArea, QSplitter, QTabWidget,
                             QVBoxLayout, QWidget)

import cnn_config as cfg
from app_styles import AppStyles, set_role
from collected_data_widget import CollectedDataWidget
from live_plot import LivePlot
from record_widget import RecordWidget
from serial_monitor import SerialMonitor
from snapshot_plot import ClipInfo, ClipPlot
from status_card import StatusCard
from take_store import TakeStore

# ============================================================================
# Constants
# ============================================================================

# DRAFT. The clip plot's share of the row it shares with the info panel.
_CLIP_PLOT_SHARE = 3
_CLIP_INFO_SHARE = 1

# ============================================================================
# Classes
# ============================================================================


class MainWindow(QMainWindow):
    """The Record tab."""

    def __init__(self, gui_commit: str) -> None:
        """
        @param gui_commit  Commit of this checkout, written into every clip.
        """
        super().__init__()
        self.setWindowTitle("CNN Record (firmware-bench, manual clips)")
        self.resize(AppStyles.WINDOW_START_WIDTH, AppStyles.WINDOW_START_HEIGHT)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        store = TakeStore(cfg.DATA_DIR)

        self.live_plot = LivePlot("Live IMU")
        self.clip_plot = ClipPlot("Clip")
        self.clip_info = ClipInfo("Clip info")
        self.card = StatusCard("Last clip")
        self.record_widget = RecordWidget(store, gui_commit, self.live_plot, self.card)
        self.collected_widget = CollectedDataWidget(store, self.clip_plot, self.clip_info)
        self.serial_monitor = SerialMonitor("Serial Monitor")
        self.top_bar = set_role(QLabel(""), "strip")
        self.top_bar.setToolTip("This session's clips on disk: per label (long NONE "
                                "recordings counted on their own), and older takes")

        # ---- Record tab
        # Left column, full height: it scrolls (vertically only) when the
        # window is too short for all three panels.
        left = set_role(QWidget(), "column")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(AppStyles.WIDGET_SPACING)
        left_layout.addWidget(self.record_widget)
        left_layout.addWidget(self.card)
        left_layout.addWidget(self.collected_widget, 1)  # The clip list gets the spare height
        left_scroll = set_role(QScrollArea(), "column")
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(AppStyles.LEFT_COLUMN_MIN_WIDTH)

        # Right column: Live IMU, then the clip plot beside its info panel,
        # then the serial monitor.
        clip_row = QSplitter(Qt.Horizontal)
        clip_row.setChildrenCollapsible(False)
        clip_row.setHandleWidth(AppStyles.WIDGET_SPACING)
        clip_row.addWidget(self.clip_plot)
        clip_row.addWidget(self.clip_info)
        clip_row.setStretchFactor(0, _CLIP_PLOT_SHARE)
        clip_row.setStretchFactor(1, _CLIP_INFO_SHARE)
        clip_row.setSizes([_CLIP_PLOT_SHARE * 1000, _CLIP_INFO_SHARE * 1000])
        for plot in (self.live_plot, self.clip_plot):
            plot.setMinimumHeight(AppStyles.PLOT_MIN_HEIGHT)

        self.right = QSplitter(Qt.Vertical)
        self.right.setChildrenCollapsible(False)  # A dragged bar never hides a panel entirely
        self.right.setHandleWidth(AppStyles.WIDGET_SPACING)
        self.right.addWidget(self.live_plot)
        self.right.addWidget(clip_row)
        self.right.addWidget(self.serial_monitor)
        self.right.setStretchFactor(0, 1)
        self.right.setStretchFactor(1, 1)
        self.right.setStretchFactor(2, 0)         # Window resizes go to the plots
        self._sized = False                       # Start sizes set on first show

        columns = QSplitter(Qt.Horizontal)
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(AppStyles.WIDGET_SPACING)
        columns.addWidget(left_scroll)
        columns.addWidget(self.right)
        columns.setStretchFactor(0, 1)       # About a third and two thirds
        columns.setStretchFactor(1, 2)
        columns.setSizes([1000, 2000])

        record_tab = QWidget()
        outer = QVBoxLayout(record_tab)
        outer.setContentsMargins(12, 8, 12, 12)
        outer.setSpacing(AppStyles.PANEL_SPACING)
        outer.addWidget(self.top_bar)
        outer.addWidget(columns, 1)
        tabs.addTab(record_tab, "Record")

        logging.getLogger().addHandler(self.serial_monitor.handler)

        # ---- Wiring
        self.record_widget.session_changed.connect(self.collected_widget.set_session)
        self.record_widget.take_saved.connect(
            lambda path: self.collected_widget.refresh(select=path))
        self.record_widget.take_deleted.connect(lambda _path: self.collected_widget.refresh())
        self.record_widget.counts_changed.connect(self.top_bar.setText)
        self.collected_widget.take_moved.connect(self.record_widget.take_moved)
        self.collected_widget.set_session(self.record_widget.ctrl.session)
        self.record_widget.refresh_counts()

    def showEvent(self, event) -> None:
        """
        First show: sets the right column's start sizes once the real window
        height is known (QSplitter.setSizes only keeps proportions).
        @param event  The show event.
        """
        super().showEvent(event)
        if not self._sized:
            self._sized = True
            QTimer.singleShot(0, self._set_start_sizes)

    def _set_start_sizes(self) -> None:
        """Serial monitor text box MONITOR_START_HEIGHT tall (less in a short
        window, so the plots keep their room); Live IMU gets a little more
        of the rest than the clip row."""
        total = sum(self.right.sizes())
        text = int(total * AppStyles.MONITOR_START_SHARE)
        text = max(AppStyles.MONITOR_MIN_HEIGHT, min(AppStyles.MONITOR_START_HEIGHT, text))
        overhead = self.serial_monitor.height() - self.serial_monitor.text.height()
        monitor = text + overhead
        plots = max(0, total - monitor)
        live = int(plots * AppStyles.LIVE_PLOT_SHARE)
        self.right.setSizes([live, plots - live, monitor])

    def closeEvent(self, event) -> None:
        """
        Saves open recordings and stops the reader thread before the window
        closes.
        @param event  The close event.
        """
        self.record_widget.shutdown()
        self.live_plot.shutdown()
        super().closeEvent(event)
