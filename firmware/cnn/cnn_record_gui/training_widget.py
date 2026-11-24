"""
File:        training_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Adapted from: Python/training_widget.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             Training tab whose button trains in a background QThread and
             reports progress and the result. Changed: every training step
             lives in firmware/cnn/training/train.py, which the command line
             runs the same way; this tab only picks the data folder and
             sessions, starts and stops a run, shows its log and opens its
             folder. No model-type choice and no matplotlib plot (the run's
             report.md holds the confusion matrices).
Description: The record GUI's Training tab (cnn-record-train-handoff.md v0.4,
             request 3). Left: data folder, sessions (ticked = used, with
             their take counts), optional test and validation sessions,
             "Allow simulated takes", Train / Stop / Open run folder, and the
             result. Right: the run log, in the same panel as the Record
             tab's serial monitor. Stop ends the run after the current epoch
             and exports nothing. TensorFlow is imported only when a run
             starts, so the GUI opens without it.
"""

# ============================================================================
# Imports
# ============================================================================

import json
import logging
import re
import sys
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QObject, Qt, QThread, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (QCheckBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QPushButton,
                             QVBoxLayout, QWidget)

import cnn_config as cfg
from app_styles import AppStyles, set_flag, set_role
from serial_monitor import SerialMonitor
from take_store import parse_take_name

if str(cfg.TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(cfg.TRAINING_DIR))
import train  # noqa: E402  (needs the path above; does not import TensorFlow)
from take_loader import list_sessions, session_type  # noqa: E402

# ============================================================================
# Constants
# ============================================================================

# Longest the window waits on close for a run to stop after the current epoch (ms).
STOP_WAIT_MS = 30000

# ============================================================================
# Classes
# ============================================================================


class _LogBridge(QObject):
    """Carries log lines from the training thread to the GUI thread."""
    line = pyqtSignal(str, int)   # Formatted record, logging level


class _ThreadLogHandler(logging.Handler):
    """Logging handler that is safe to call from the training thread: it only
    emits a Qt signal, which Qt queues to the GUI thread."""

    def __init__(self, bridge: _LogBridge) -> None:
        """
        @param bridge  Where the lines go.
        """
        super().__init__()
        self.bridge = bridge

    def emit(self, record: logging.LogRecord) -> None:
        """
        @param record  One log record.
        """
        try:
            self.bridge.line.emit(self.format(record), record.levelno)
        except Exception:  # A broken log line must never stop a run
            self.handleError(record)


class TrainingWorker(QThread):
    """Runs train.run_training() off the GUI thread."""
    succeeded = pyqtSignal(str)       # Run folder
    failed = pyqtSignal(str, str)     # Message, run folder ("" if none)

    def __init__(self, opts) -> None:
        """
        @param opts  train.RunOptions.
        """
        super().__init__()
        self.opts = opts
        self._stop = False

    def request_stop(self) -> None:
        """Asks the run to stop after the current epoch."""
        self._stop = True

    def run(self) -> None:
        """Thread body: one run; the outcome goes out as a signal."""
        try:
            run_dir = train.run_training(self.opts, should_stop=lambda: self._stop)
            self.succeeded.emit(str(run_dir))
        except train.TrainingError as exc:
            self.failed.emit(str(exc), str(exc.run_dir or ""))
        except Exception as exc:  # A bug: train.log already has the traceback
            self.failed.emit("Unexpected error: %s" % exc, "")


class TrainingWidget(QWidget):
    """The Training tab: controls on the left, the run log on the right."""

    def __init__(self) -> None:
        super().__init__()
        self.data_dir = Path(cfg.DATA_DIR)
        self.worker: Optional[TrainingWorker] = None
        self.last_run: Optional[Path] = None

        # ---- Left: the controls
        box = QGroupBox("Training")
        box.setMinimumWidth(AppStyles.LEFT_COLUMN_MIN_WIDTH)
        form = QVBoxLayout(box)
        form.setSpacing(AppStyles.PANEL_SPACING)

        form.addWidget(QLabel("Data folder"))
        self.dir_label = set_role(QLabel(""), "info")
        self.dir_label.setWordWrap(True)
        self.dir_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addWidget(self.dir_label)
        dir_row = QHBoxLayout()
        choose = QPushButton("Choose folder...")
        choose.clicked.connect(self._choose_folder)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        dir_row.addWidget(choose)
        dir_row.addWidget(refresh)
        form.addLayout(dir_row)

        form.addWidget(QLabel("Sessions (ticked ones are used)"))
        self.session_list = QListWidget()
        self.session_list.setMinimumHeight(AppStyles.TAKE_LIST_MIN_HEIGHT)
        form.addWidget(self.session_list, 1)

        grid = QGridLayout()
        grid.addWidget(QLabel("Test sessions"), 0, 0)
        self.test_edit = QLineEdit()
        self.test_edit.setPlaceholderText("automatic (seed %d)" % cfg.TRAIN_SEED)
        self.test_edit.setToolTip("Session names separated by commas; blank = one in %d "
                                  "sessions, picked with the seed" % cfg.SPLIT_HOLDOUT_EVERY)
        grid.addWidget(self.test_edit, 0, 1)
        grid.addWidget(QLabel("Validation sessions"), 1, 0)
        self.val_edit = QLineEdit()
        self.val_edit.setPlaceholderText("automatic (seed %d)" % cfg.TRAIN_SEED)
        self.val_edit.setToolTip("Session names separated by commas; used for early stopping")
        grid.addWidget(self.val_edit, 1, 1)
        form.addLayout(grid)

        self.sim_check = QCheckBox("Allow simulated takes (sample sets only)")
        form.addWidget(self.sim_check)

        buttons = QHBoxLayout()
        self.train_button = QPushButton("Train")
        self.train_button.clicked.connect(self._start)
        self.stop_button = QPushButton("Stop")
        self.stop_button.setToolTip("Stop after the current epoch; nothing is exported")
        self.stop_button.clicked.connect(self._stop)
        self.open_button = QPushButton("Open run folder")
        self.open_button.clicked.connect(self._open_run)
        for b in (self.train_button, self.stop_button, self.open_button):
            buttons.addWidget(b)
        form.addLayout(buttons)

        self.status = set_role(QLabel("Pick the sessions, then press Train."), "strip")
        self.status.setWordWrap(True)
        form.addWidget(self.status)
        note = set_role(QLabel("Each run is saved in firmware/cnn/training/runs/<date_time>/ "
                               "(model, model_data.h, report.md, frozen test set, train.log). "
                               "The command line does the same: "
                               "firmware/cnn/training/README.md."), "note")
        note.setWordWrap(True)
        form.addWidget(note)

        # ---- Right: the run log
        self.monitor = SerialMonitor("Training log")
        self._bridge = _LogBridge()
        self._bridge.line.connect(self.monitor.append_line)
        self._handler = _ThreadLogHandler(self._bridge)
        self._handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                                     "%H:%M:%S"))
        log = logging.getLogger(train.LOGGER_NAME)
        log.addHandler(self._handler)
        log.propagate = False   # Runs have their own train.log; keep them off the Record tab

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 12)
        layout.setSpacing(AppStyles.WIDGET_SPACING)
        layout.addWidget(box, 1)
        layout.addWidget(self.monitor, 2)

        self._set_running(False)
        self.refresh()

    # ---------------------------------------------------------------- actions

    def refresh(self) -> None:
        """Lists the data folder's sessions with their take counts, keeping
        the ticks of sessions already listed."""
        self.dir_label.setText(str(self.data_dir))
        unticked = {self.session_list.item(i).data(Qt.UserRole)
                    for i in range(self.session_list.count())
                    if self.session_list.item(i).checkState() != Qt.Checked}
        self.session_list.clear()
        for name in list_sessions(self.data_dir):
            labeled = unlabeled = 0
            for path in (self.data_dir / name).glob("*.csv"):
                parsed = parse_take_name(path)
                if parsed is None:
                    continue
                if parsed[0] == cfg.UNLABELED:
                    unlabeled += 1
                else:
                    labeled += 1
            text = "%s  (%s, %d takes%s)" % (name, session_type(name), labeled,
                                             ", %d unlabeled" % unlabeled if unlabeled else "")
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, name)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if name in unticked else Qt.Checked)
            self.session_list.addItem(item)
        if self.session_list.count() == 0:
            self._show("No session folders in this data folder.", error=False)

    def shutdown(self) -> None:
        """Window closing: stops a running run after its current epoch."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            if not self.worker.wait(STOP_WAIT_MS):
                logging.getLogger("record").warning("Training run still busy at close")
        logging.getLogger(train.LOGGER_NAME).removeHandler(self._handler)

    # ---------------------------------------------------------------- helpers

    def _choose_folder(self) -> None:
        """Picks another data folder (e.g. a sample set)."""
        folder = QFileDialog.getExistingDirectory(self, "Data folder", str(self.data_dir))
        if folder:
            self.data_dir = Path(folder)
            self.refresh()

    @staticmethod
    def _names(text: str) -> Optional[list]:
        """
        @param text  Session names separated by commas or spaces.
        @return The names, or None if there are none (automatic).
        """
        names = [n for n in re.split(r"[,\s]+", text.strip()) if n]
        return names or None

    def _start(self) -> None:
        """Starts a run with the ticked sessions."""
        sessions = [self.session_list.item(i).data(Qt.UserRole)
                    for i in range(self.session_list.count())
                    if self.session_list.item(i).checkState() == Qt.Checked]
        if not sessions:
            self._show("Tick at least one session.", error=True)
            return
        opts = train.RunOptions(data_dir=self.data_dir, sessions=sessions,
                                test_sessions=self._names(self.test_edit.text()),
                                val_sessions=self._names(self.val_edit.text()),
                                allow_sim=self.sim_check.isChecked())
        self.worker = TrainingWorker(opts)
        self.worker.succeeded.connect(self._on_succeeded)
        self.worker.failed.connect(self._on_failed)
        self._set_running(True)
        self._show("Training... (the first run loads TensorFlow, which takes a few seconds)",
                   error=False)
        self.worker.start()

    def _stop(self) -> None:
        """Asks the running run to stop after the current epoch."""
        if self.worker is not None and self.worker.isRunning():
            self.worker.request_stop()
            self.stop_button.setEnabled(False)
            self._show("Stopping after the current epoch...", error=False)

    def _on_succeeded(self, run_dir: str) -> None:
        """
        @param run_dir  The finished run's folder.
        """
        self.last_run = Path(run_dir)
        self._set_running(False)
        result = ""
        try:
            data = json.loads((self.last_run / "report.json").read_text(encoding="utf-8"))
            result = "REPORTABLE" if data["metrics"]["reportable"] else \
                "NOT REPORTABLE (a class below %d takes)" % cfg.MIN_TAKES_PER_CLASS
        except (OSError, ValueError, KeyError):
            pass
        self._show("Done: %s. %s Open the run folder for report.md." % (self.last_run.name,
                                                                         result), error=False)

    def _on_failed(self, message: str, run_dir: str) -> None:
        """
        @param message  Why the run stopped.
        @param run_dir  Its folder (holds train.log), or "".
        """
        if run_dir:
            self.last_run = Path(run_dir)
        self._set_running(False)
        self._show("Run stopped: %s" % message.splitlines()[0], error=True)

    def _open_run(self) -> None:
        """Opens the last run's folder in File Explorer."""
        if self.last_run is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_run)))

    def _set_running(self, running: bool) -> None:
        """
        @param running  True while a run is busy.
        """
        self.train_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.open_button.setEnabled(not running and self.last_run is not None)
        set_flag(self.train_button, "armed", running)

    def _show(self, text: str, error: bool) -> None:
        """
        @param text   Status line.
        @param error  Show it in the error color.
        """
        self.status.setText(text)
        set_flag(self.status, "role", "error" if error else "strip")
