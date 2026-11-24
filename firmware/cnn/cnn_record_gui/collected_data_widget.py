"""
File:        collected_data_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.4.0
Change:      0.4.0 (manual-recording-overhaul-handoff.md, request B steps 5
             and 6): "Collected clips". Newest first, so a clip just saved is
             at the top (and selected). Selecting a clip plots it (clip_file
             load_clip()) and shows its plain facts in the info panel.
             Relabel offers POTHOLE, SPEED_BUMP and NONE. Older snapshot-era
             takes are listed, viewed, relabeled and deleted the same way and
             tagged "older take"; long recordings are tagged "long". Nothing
             is colored, hidden or flagged because of a clip's facts (O3).
             0.3.0: manual and log tags (requests 5, 8).
Adapted from: Python/collected_data_widget.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             list, view and delete. Changed: lists the current session's
             clip files (TakeStore) instead of data/*.txt; adds Relabel;
             Delete asks first and moves the clip to <session>/_deleted/;
             "Delete All" is removed (too easy to lose a session).
Description: The "Collected clips" panel: the current session's clips, newest
             first. Selecting one plots it and fills the info panel; Delete
             and Relabel act on the selected clip.
"""

# ============================================================================
# Imports
# ============================================================================

from pathlib import Path
from typing import Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (QApplication, QComboBox, QGroupBox, QHBoxLayout, QLabel,
                             QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                             QSizePolicy, QVBoxLayout)

import cnn_config as cfg
from app_styles import AppStyles, set_role
from clip_file import KIND_CLIP, KIND_TAKE, clip_kind, info_lines, load_clip
from snapshot_plot import ClipInfo, ClipPlot
from take_store import TakeStore, read_header

# ============================================================================
# Classes
# ============================================================================


class CollectedDataWidget(QGroupBox):
    """List of the session's clips, with view, relabel and delete."""

    # (old path, new path or None if deleted): lets the record panel keep its
    # "last saved clip" (Backspace) and the counts right.
    take_moved = pyqtSignal(object, object)

    def __init__(self, store: TakeStore, plot: ClipPlot, info: ClipInfo) -> None:
        """
        @param store  Where clips live.
        @param plot   Where the selected clip is drawn.
        @param info   Where its facts are listed.
        """
        super().__init__("Collected clips")
        self.store = store
        self.plot = plot
        self.info = info
        self.session = ""

        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)

        self.count_label = QLabel("")
        self.count_label.setWordWrap(True)
        layout.addWidget(self.count_label)

        self.take_list = QListWidget()
        self.take_list.setMinimumHeight(AppStyles.TAKE_LIST_MIN_HEIGHT)
        # Takes the spare height only: it never makes the left column scroll
        # while it could still shrink to its minimum.
        self.take_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Ignored)
        self.take_list.currentItemChanged.connect(self._on_selected)
        layout.addWidget(self.take_list, 1)

        relabel_row = QHBoxLayout()
        self.relabel_combo = QComboBox()
        self.relabel_combo.addItems(cfg.LABELS)
        self.relabel_button = QPushButton("Relabel")
        self.relabel_button.clicked.connect(self._relabel_selected)
        relabel_row.addWidget(self.relabel_combo, 1)
        relabel_row.addWidget(self.relabel_button)
        layout.addLayout(relabel_row)

        buttons = QHBoxLayout()
        self.delete_button = QPushButton("Delete clip")
        self.delete_button.clicked.connect(self._delete_selected)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(lambda: self.refresh())
        buttons.addWidget(self.delete_button)
        buttons.addWidget(self.refresh_button)
        layout.addLayout(buttons)

        note = set_role(QLabel("You decide every clip: keep it, relabel it or delete it. Delete "
                               "moves it to the session's %s folder." % cfg.DELETED_DIR_NAME),
                        "note")
        note.setWordWrap(True)
        layout.addWidget(note)

    def set_session(self, session: str) -> None:
        """
        Shows another session's clips.
        @param session  A valid session name.
        """
        self.session = session
        self.refresh()

    def refresh(self, select: Optional[Path] = None) -> None:
        """
        Reloads the list from disk, newest first.
        @param select  Clip to select afterwards; default keeps the current one.
        """
        if select is None:
            select = self._selected_path()
        self.take_list.blockSignals(True)  # One plot update at the end, not per item
        self.take_list.clear()
        paths = []
        if self.session:
            try:
                paths = list(reversed(self.store.list_takes(self.session)))
            except (OSError, ValueError) as exc:
                self.count_label.setText("Cannot list clips: %s" % exc)
        clips = older = 0
        for path in paths:
            try:
                header = read_header(path)
            except OSError:
                header = {}
            kind = clip_kind(header)
            tag = None
            if kind == KIND_TAKE:
                tag = "older take"
                older += 1
            elif kind == KIND_CLIP:
                clips += 1
                if header.get("source") == cfg.SOURCE_LONG:
                    tag = "long, %s s" % header.get("post_s", "?")
            item = QListWidgetItem("%s  (%s)" % (path.name, tag) if tag else path.name)
            item.setData(Qt.UserRole, str(path))
            self.take_list.addItem(item)
            if select is not None and path == select:
                self.take_list.setCurrentItem(item)
        self.take_list.blockSignals(False)
        text = "%s: %d clips" % (self.session or "no session", clips)
        if older:
            text += ", %d older takes" % older
        self.count_label.setText(text)
        self._on_selected(self.take_list.currentItem(), None)

    def _selected_path(self) -> Optional[Path]:
        """
        @return The selected clip's path, or None.
        """
        item = self.take_list.currentItem()
        return Path(item.data(Qt.UserRole)) if item is not None else None

    def _on_selected(self, item, _previous) -> None:
        """
        Plots the selected clip and lists its facts.
        @param item       The selected QListWidgetItem, or None.
        @param _previous  Unused (Qt passes the previous item).
        """
        if item is None:
            self.plot.clear("")
            self.info.clear("Select a clip in Collected clips.")
            return
        path = Path(item.data(Qt.UserRole))
        QApplication.setOverrideCursor(Qt.WaitCursor)   # A long recording takes a moment
        try:
            clip = load_clip(path)
        except (OSError, ValueError) as exc:
            self.plot.clear("Cannot read %s: %s" % (path.name, exc))
            self.info.clear("Cannot read %s: %s" % (path.name, exc))
            return
        finally:
            QApplication.restoreOverrideCursor()
        h = clip.header
        when = h.get("press_pc") or h.get("pc_received") or h.get("pc_time_est", "")
        self.plot.show_clip(clip.model, clip.rate_hz, clip.press_row,
                            "%s  label %s  %s  %d rows  %s" % (path.name, clip.label, clip.source,
                                                               clip.rows, when))
        self.info.show_lines(info_lines(clip))

    def _relabel_selected(self) -> None:
        """Moves the selected clip under the label chosen in the dropdown."""
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
        """Moves the selected clip to _deleted, after asking."""
        path = self._selected_path()
        if path is None:
            return
        answer = QMessageBox.question(self, "Delete clip",
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
