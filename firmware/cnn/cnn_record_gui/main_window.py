"""
File:        main_window.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Adapted from: Python/main_window.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             window, its column layout and spacing. Changed: two tabs only,
             Record and Training; the Codegen and Live Inference tabs and the
             inference widget are removed (handoff); a log panel and a live
             IMU view are added.
Description: Main window of the CNN record GUI. Record tab: the record panel
             and collected takes on the left; on the right, the live IMU view
             on top, then the last snapshot and the selected take (a splitter:
             drag the bars between them to resize); the log below. Training
             tab: a placeholder until request 3 (training and export).
"""

# ============================================================================
# Imports
# ============================================================================

import logging

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit, QSplitter,
                             QTabWidget, QVBoxLayout, QWidget)

import cnn_config as cfg
from app_styles import AppStyles
from collected_data_widget import CollectedDataWidget
from live_plot import LivePlot
from record_widget import RecordWidget
from snapshot_plot import SnapshotPlot
from take_store import TakeStore

# ============================================================================
# Classes
# ============================================================================


class QtLogHandler(logging.Handler):
    """Copies log records into a text panel. Records come from the GUI thread
    only (the reader thread reports through Qt signals, never the log)."""

    def __init__(self, panel: QPlainTextEdit) -> None:
        """
        @param panel  Where the lines go.
        """
        super().__init__()
        self.panel = panel

    def emit(self, record: logging.LogRecord) -> None:
        """
        @param record  One log record.
        """
        self.panel.appendPlainText(self.format(record))


class MainWindow(QMainWindow):
    """Record and Training tabs."""

    def __init__(self, gui_commit: str) -> None:
        """
        @param gui_commit  Commit of this checkout, written into every take.
        """
        super().__init__()
        self.setWindowTitle("CNN Record (firmware-bench record mode)")
        self.resize(1400, 900)

        tabs = QTabWidget()
        self.setCentralWidget(tabs)
        store = TakeStore(cfg.DATA_DIR)

        # ---- Record tab
        record_tab = QWidget()
        outer = QVBoxLayout(record_tab)
        main_layout = QHBoxLayout()
        main_layout.setSpacing(AppStyles.WIDGET_SPACING)
        outer.addLayout(main_layout, 1)

        self.live_plot = LivePlot("Live IMU")
        self.last_plot = SnapshotPlot("Last snapshot")
        self.take_plot = SnapshotPlot("Selected take")
        self.record_widget = RecordWidget(store, gui_commit, self.last_plot, self.live_plot)
        self.collected_widget = CollectedDataWidget(store, self.take_plot)

        left = QVBoxLayout()
        left.setSpacing(AppStyles.WIDGET_SPACING)
        left.addWidget(self.record_widget, 3)
        left.addWidget(self.collected_widget, 2)
        main_layout.addLayout(left, 2)

        right = QSplitter(Qt.Vertical)
        right.setChildrenCollapsible(False)  # A dragged bar never hides a plot entirely
        for plot in (self.live_plot, self.last_plot, self.take_plot):
            plot.setMinimumHeight(160)
            right.addWidget(plot)
        right.setSizes([1000, 1000, 1000])   # Equal thirds to start
        main_layout.addWidget(right, 3)

        self.log_panel = QPlainTextEdit()
        self.log_panel.setReadOnly(True)
        self.log_panel.setMaximumBlockCount(500)   # Recent lines; the file keeps all
        self.log_panel.setFixedHeight(130)
        self.log_panel.setStyleSheet(AppStyles.STATUS_STYLE)
        outer.addWidget(self.log_panel)
        handler = QtLogHandler(self.log_panel)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                               "%H:%M:%S"))
        logging.getLogger().addHandler(handler)

        tabs.addTab(record_tab, "Record")

        # ---- Training tab (request 3)
        training_tab = QWidget()
        t_layout = QVBoxLayout(training_tab)
        placeholder = QLabel("Training and export come in request 3 (firmware/cnn/training/), "
                             "after decisions C10 (normalization), C11 (split and minimum "
                             "takes) and C12 (Python and package versions).\n\nTakes "
                             "recorded here are saved under:\n%s" % cfg.DATA_DIR)
        placeholder.setWordWrap(True)
        placeholder.setStyleSheet(AppStyles.get_label_style())
        t_layout.addWidget(placeholder)
        t_layout.addStretch(1)
        tabs.addTab(training_tab, "Training")

        # ---- Wiring
        self.record_widget.session_changed.connect(self.collected_widget.set_session)
        self.record_widget.take_saved.connect(
            lambda path: self.collected_widget.refresh(select=path))
        self.collected_widget.take_moved.connect(self.record_widget.take_moved)
        self.collected_widget.set_session(self.record_widget.ctrl.session)

    def closeEvent(self, event) -> None:
        """
        Stops the reader thread before the window closes.
        @param event  The close event.
        """
        self.record_widget.shutdown()
        self.live_plot.shutdown()
        super().closeEvent(event)
