"""
File:        serial_monitor.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Adapted from: the "Serial Monitor" panel in Python/live_inference_widget.py
             in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             dark monospace log panel with Auto-scroll, Pause and Clear.
             Changed (record-gui-restyle-handoff.md v0.1, request 7): it
             shows the GUI's own log records (the lines the old log box
             showed), not raw serial lines; no log modes and no command
             input; warnings amber, errors red.
Description: The "Serial Monitor" panel: the GUI's log records as
             "HH:MM:SS LEVEL message", the most recent 500 (the log file
             keeps them all). Pause freezes the view and shows the lines that
             came meanwhile on resume (still at most 500); Clear empties the
             view only. QtLogHandler moved here from main_window.py; it
             formats lines exactly as before.
"""

# ============================================================================
# Imports
# ============================================================================

import logging
from collections import deque

from PyQt5.QtGui import QColor, QTextBlockFormat, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import (QCheckBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton,
                             QVBoxLayout, QWidget)

from app_styles import AppStyles, set_flag, set_role

# ============================================================================
# Constants
# ============================================================================

MAX_LINES = 500  # Recent lines kept on screen; the log file keeps all

# ============================================================================
# Classes
# ============================================================================


class SerialMonitor(QWidget):
    """The log panel at the bottom of the right column: a heading row with
    the controls on it (no frame, so the text box gets the height), then the
    text box."""

    def __init__(self, title: str = "Serial Monitor") -> None:
        """
        @param title  Panel heading.
        """
        super().__init__()
        self._held = deque(maxlen=MAX_LINES)   # Lines that came while paused

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        controls = QHBoxLayout()
        controls.setContentsMargins(2, 0, 0, 0)  # Heading lines up with the panel headings
        controls.addWidget(set_role(QLabel(title), "heading"))
        controls.addStretch(1)
        self.auto_scroll = QCheckBox("Auto-scroll")
        self.auto_scroll.setChecked(True)
        self.auto_scroll.setToolTip("Keep the newest line in view")
        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.setToolTip("Freeze the view; lines that arrive meanwhile appear on "
                                     "Resume")
        self.pause_button.toggled.connect(self._on_pause)
        self.clear_button = QPushButton("Clear")
        self.clear_button.setToolTip("Empty the view (the log file keeps every line)")
        self.clear_button.clicked.connect(self._clear)
        for widget in (self.auto_scroll, self.pause_button, self.clear_button):
            controls.addWidget(widget)
        layout.addLayout(controls)

        # Read-only plain text, as before: it never takes the P / B / F keys.
        self.text = set_role(QPlainTextEdit(), "monitor")
        self.text.setReadOnly(True)
        self.text.setUndoRedoEnabled(False)   # Lines are only added: no undo history
        self.text.setMaximumBlockCount(MAX_LINES)
        self.text.setMinimumHeight(AppStyles.MONITOR_MIN_HEIGHT)
        layout.addWidget(self.text)

        self.handler = QtLogHandler(self)
        self.handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s",
                                                    "%H:%M:%S"))

    def append_line(self, line: str, level: int) -> None:
        """
        @param line   One formatted log record (may hold line breaks).
        @param level  Its logging level: WARNING lines amber, ERROR red.
        """
        if self.pause_button.isChecked():
            self._held.append((line, level))
        else:
            self._write(line, level)

    # ---------------------------------------------------------------- helpers

    def _write(self, line: str, level: int) -> None:
        """
        Adds one record at the end, in its level's color.
        @param line   The text.
        @param level  Its logging level.
        """
        fmt = QTextCharFormat()
        if level >= logging.ERROR:
            fmt.setForeground(QColor(AppStyles.ACCENT_ERROR))
        elif level >= logging.WARNING:
            fmt.setForeground(QColor(AppStyles.ACCENT_WARN))
        bar = self.text.verticalScrollBar()
        where = bar.value()
        cursor = QTextCursor(self.text.document())
        cursor.movePosition(QTextCursor.End)
        if not self.text.document().isEmpty():
            cursor.insertBlock(QTextBlockFormat(), fmt)
        cursor.insertText(line, fmt)
        bar.setValue(bar.maximum() if self.auto_scroll.isChecked() else where)

    def _on_pause(self, paused: bool) -> None:
        """
        @param paused  Pause button state. Resuming shows the held lines.
        """
        self.pause_button.setText("Resume" if paused else "Pause")
        set_flag(self.pause_button, "armed", paused)
        if not paused:
            while self._held:
                self._write(*self._held.popleft())

    def _clear(self) -> None:
        """Empties the view; held lines (if paused) still appear on resume."""
        self.text.clear()


class QtLogHandler(logging.Handler):
    """Copies log records into the serial monitor. Records come from the GUI
    thread only (the reader thread reports through Qt signals, never the log)."""

    def __init__(self, monitor: SerialMonitor) -> None:
        """
        @param monitor  Where the lines go.
        """
        super().__init__()
        self.monitor = monitor

    def emit(self, record: logging.LogRecord) -> None:
        """
        @param record  One log record.
        """
        self.monitor.append_line(self.format(record), record.levelno)
