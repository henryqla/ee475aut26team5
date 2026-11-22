"""
File:        main_window.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.3.0
Change:      0.3.0: the Training tab runs training (request 3,
             training_widget.py) instead of showing a placeholder.
Adapted from: Python/main_window.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             window, the Playground tab's layout (controls and takes in a
             left column, a big live plot top right, the take plot below it)
             and its spacing. Changed: two tabs only, Record and Training;
             the Codegen and Live Inference tabs and the inference widget
             are removed (handoff); a live IMU view, a status card and a
             serial monitor (from the Live Inference tab) are added, and the
             parts can be resized with splitters (request 7).
Description: Main window of the CNN record GUI. Record tab: on the left, the
             Record panel, the Last take card and Collected takes, the full
             window height (the column scrolls if the window is too short);
             on the right, Live IMU on top, Last snapshot and Selected take
             side by side below it, and the serial monitor at the bottom.
             Drag the gaps between them to resize. Training tab: picks
             sessions and runs training and export (training_widget.py).
"""

# ============================================================================
# Imports
# ============================================================================

import logging

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (QFrame, QHBoxLayout, QMainWindow, QScrollArea, QSplitter,
                             QTabWidget, QVBoxLayout, QWidget)

import cnn_config as cfg
from app_styles import AppStyles, set_role
from collected_data_widget import CollectedDataWidget
from live_plot import LivePlot
from record_widget import RecordWidget
from serial_monitor import SerialMonitor
from snapshot_plot import SnapshotPlot
from status_card import StatusCard
from take_store import TakeStore
from training_widget import TrainingWidget

# ============================================================================
# Classes
# ============================================================================


class MainWindow(QMainWindow):
    """Record and Training tabs."""

    def __init__(self, gui_commit: str) -> None:
        """
        @param gui_commit  Commit of this checkout, written into every take.
        """
        super().__init__()
        self.setWindowTitle("CNN Record (firmware-bench record mode)")
        self.resize(AppStyles.WINDOW_START_WIDTH, AppStyles.WINDOW_START_HEIGHT)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        store = TakeStore(cfg.DATA_DIR)

        self.live_plot = LivePlot("Live IMU")
        self.last_plot = SnapshotPlot("Last snapshot")
        self.take_plot = SnapshotPlot("Selected take")
        self.card = StatusCard("Last take")
        self.record_widget = RecordWidget(store, gui_commit, self.last_plot, self.live_plot,
                                          self.card)
        self.collected_widget = CollectedDataWidget(store, self.take_plot)
        self.serial_monitor = SerialMonitor("Serial Monitor")

        # ---- Record tab
        # Left column, full height: it scrolls (vertically only) when the
        # window is too short for all three panels.
        left = set_role(QWidget(), "column")
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(AppStyles.WIDGET_SPACING)
        left_layout.addWidget(self.record_widget)
        left_layout.addWidget(self.card)
        left_layout.addWidget(self.collected_widget, 1)  # The take list gets the spare height
        left_scroll = set_role(QScrollArea(), "column")
        left_scroll.setWidget(left)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left_scroll.setMinimumWidth(AppStyles.LEFT_COLUMN_MIN_WIDTH)

        # Right column: Live IMU, then the two snapshot plots side by side,
        # then the serial monitor.
        snapshot_row = QWidget()
        row_layout = QHBoxLayout(snapshot_row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(AppStyles.WIDGET_SPACING)
        row_layout.addWidget(self.last_plot)
        row_layout.addWidget(self.take_plot)
        for plot in (self.live_plot, self.last_plot, self.take_plot):
            plot.setMinimumHeight(AppStyles.PLOT_MIN_HEIGHT)

        self.right = QSplitter(Qt.Vertical)
        self.right.setChildrenCollapsible(False)  # A dragged bar never hides a panel entirely
        self.right.setHandleWidth(AppStyles.WIDGET_SPACING)
        self.right.addWidget(self.live_plot)
        self.right.addWidget(snapshot_row)
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
        outer.addWidget(columns)
        tabs.addTab(record_tab, "Record")

        logging.getLogger().addHandler(self.serial_monitor.handler)

        # ---- Training tab (request 3): runs firmware/cnn/training/train.py
        self.training_widget = TrainingWidget()
        tabs.addTab(self.training_widget, "Training")

        # ---- Wiring
        self.record_widget.session_changed.connect(self.collected_widget.set_session)
        self.record_widget.take_saved.connect(
            lambda path: self.collected_widget.refresh(select=path))
        self.collected_widget.take_moved.connect(self.record_widget.take_moved)
        self.collected_widget.set_session(self.record_widget.ctrl.session)

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
        of the rest than the snapshot row."""
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
        Stops the reader thread, and a training run after its current
        epoch, before the window closes.
        @param event  The close event.
        """
        self.record_widget.shutdown()
        self.live_plot.shutdown()
        self.training_widget.shutdown()
        super().closeEvent(event)
