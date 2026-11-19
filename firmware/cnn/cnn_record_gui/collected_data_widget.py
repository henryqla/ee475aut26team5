"""
File:        collected_data_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Adapted from: Python/collected_data_widget.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             list, view and delete. Changed: lists the current session's
             take files (TakeStore) instead of data/*.txt; adds Relabel;
             Delete asks first and moves the take to <session>/_deleted/;
             "Delete All" is removed (too easy to lose a session).
Description: The "Collected takes" panel: the current session's takes,
             oldest first. Selecting one plots it; Delete and Relabel act on
             the selected take.
"""

# ============================================================================
# Imports
# ============================================================================

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QComboBox, QGroupBox, QHBoxLayout, QLabel, QListWidget,
                             QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout)

import cnn_config as cfg
from app_styles import AppStyles
from snapshot_plot import SnapshotPlot
from take_store import TakeStore, load_take

# ============================================================================
# Classes
# ============================================================================


class CollectedDataWidget(QGroupBox):
    """List of the session's takes, with view, relabel and delete."""

    # (old path, new path or None if deleted): lets the record widget keep
    # its "most recent take" pointer right.
    take_moved = pyqtSignal(object, object)

    def __init__(self, store: TakeStore, plot: SnapshotPlot) -> None:
        """
        @param store  Where takes live.
        @param plot   Where the selected take is drawn.
        """
        super().__init__("Collected takes")
        self.setStyleSheet(AppStyles.get_group_box_style())
        self.store = store
        self.plot = plot
        self.session = ""

        layout = QVBoxLayout(self)

        self.count_label = QLabel("")
        self.count_label.setStyleSheet(AppStyles.get_label_style())
        layout.addWidget(self.count_label)

        self.take_list = QListWidget()
        self.take_list.setStyleSheet(AppStyles.get_list_widget_style())
        self.take_list.currentItemChanged.connect(self._on_selected)
        layout.addWidget(self.take_list, 1)

        relabel_row = QHBoxLayout()
        self.relabel_combo = QComboBox()
        self.relabel_combo.setStyleSheet(AppStyles.get_combo_box_style())
        self.relabel_combo.addItems(cfg.LABELS)
        self.relabel_button = QPushButton("Relabel")
        self.relabel_button.setStyleSheet(AppStyles.get_button_style())
        self.relabel_button.clicked.connect(self._relabel_selected)
        relabel_row.addWidget(self.relabel_combo, 1)
        relabel_row.addWidget(self.relabel_button)
        layout.addLayout(relabel_row)

        buttons = QHBoxLayout()
        self.delete_button = QPushButton("Delete take")
        self.delete_button.setStyleSheet(AppStyles.get_button_style())
        self.delete_button.clicked.connect(self._delete_selected)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet(AppStyles.get_button_style())
        self.refresh_button.clicked.connect(lambda: self.refresh())
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons)

        note = QLabel("Delete moves the take to the session's _deleted folder.")
        note.setStyleSheet(AppStyles.get_note_label_style())
        layout.addWidget(note)

    def set_session(self, session: str) -> None:
        """
        Shows another session's takes.
        @param session  A valid session name.
        """
        self.session = session
        self.refresh()

    def refresh(self, select: Optional[Path] = None) -> None:
        """
        Reloads the list from disk.
        @param select  Take to select afterwards; default keeps the current one.
        """
        if select is None:
            select = self._selected_path()
        self.take_list.blockSignals(True)  # One plot update at the end, not per item
        self.take_list.clear()
        takes = []
        if self.session:
            try:
                takes = self.store.list_takes(self.session)
            except (OSError, ValueError) as exc:
                self.count_label.setText("Cannot list takes: %s" % exc)
        counts = {}
        for path in takes:
            item = QListWidgetItem(path.name)
            item.setData(Qt.UserRole, str(path))
            self.take_list.addItem(item)
            label = path.stem.rpartition("-")[0]
            counts[label] = counts.get(label, 0) + 1
            if select is not None and path == select:
                self.take_list.setCurrentItem(item)
        self.take_list.blockSignals(False)
        summary = ", ".join("%s %d" % (k, counts.get(k, 0)) for k in cfg.LABELS)
        self.count_label.setText("%s: %d takes (%s)" % (self.session or "no session",
                                                       len(takes), summary))
        self._on_selected(self.take_list.currentItem(), None)

    def _selected_path(self) -> Optional[Path]:
        """
        @return The selected take's path, or None.
        """
        item = self.take_list.currentItem()
        return Path(item.data(Qt.UserRole)) if item is not None else None

    def _on_selected(self, item, _previous) -> None:
        """
        Plots the selected take.
        @param item       The selected QListWidgetItem, or None.
        @param _previous  Unused (Qt passes the previous item).
        """
        if item is None:
            self.plot.clear("")
            return
        path = Path(item.data(Qt.UserRole))
        try:
            header, values = load_take(path)
            n_pre = int(header.get("n_pre", "0"))
            rate = float(header.get("rate_hz", "416"))
        except (OSError, ValueError) as exc:
            self.plot.clear("Cannot read %s: %s" % (path.name, exc))
            return
        self.plot.show_values(values, n_pre, rate,
                              "%s  label %s  seq %s  rule %s  fw %s  %s" % (
                                  path.name, header.get("label", "?"), header.get("seq", "?"),
                                  header.get("rule_class", "?"), header.get("fw", "?"),
                                  header.get("pc_received", "")))

    def _relabel_selected(self) -> None:
        """Moves the selected take under the label chosen in the dropdown."""
        path = self._selected_path()
        if path is None:
            return
        try:
            new = self.store.relabel(path, self.relabel_combo.currentText())
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Relabel failed", str(exc))
            return
        if new != path:
            self.take_moved.emit(path, new)
        self.refresh(select=new)

    def _delete_selected(self) -> None:
        """Moves the selected take to _deleted, after asking."""
        path = self._selected_path()
        if path is None:
            return
        answer = QMessageBox.question(self, "Delete take",
                                      "Move %s to %s/?" % (path.name, cfg.DELETED_DIR_NAME),
                                      QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if answer != QMessageBox.Yes:
            return
        try:
            self.store.delete(path)
        except OSError as exc:
            QMessageBox.warning(self, "Delete failed", str(exc))
            return
        self.take_moved.emit(path, None)
        self.refresh()
