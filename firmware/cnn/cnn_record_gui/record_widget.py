"""
File:        record_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.4.1
Change:      0.4.1 (training-live-inference-handoff.md, request G): the
             reader thread is shared with the Live Inference tab
             (handler_started, emitted with each new SerialHandler), and the
             record keys work only on the Record tab (set_keys_enabled, P7).
             A model fault (FLT F5-F8: the board's inference stopped, while
             streaming goes on; owner, 2026-11-24) shows in the device strip
             and is logged once per connection, instead of the card's
             DEVICE FAULT. Firmware texts name 0.1.5-bench. Recording is
             unchanged.
             0.4.0 (manual-recording-overhaul-handoff.md, request B): a
             one-key manual recorder. P / B / N (or their buttons) label and
             record a clip from MANUAL_PRE_S before the press to MANUAL_POST_S
             after it, with a countdown on the card and a tone at the press
             and at the save; every press is saved (O3). L starts and stops a
             long NONE recording. Backspace moves the last clip saved in this
             run to _deleted. The session's clip counts go to the top bar.
             With the simulated device, test controls inject an event after a
             press, a gap, a reset, ok=0 rows or a USB drop-out. Removed (git
             tag pre-manual-overhaul): Arm, keep armed, the label dropdown,
             quick labels, snapshots, the drive log, marks, manual Record with
             alignment, and the record / log build switch.
             0.3.x: log build, drive log, manual Record, CFG settings.
Adapted from: Python/record_data_widget.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             QGroupBox with record buttons and notes, and a recording of a
             fixed length per press. Changed: one key per label both labels
             and records; the clip comes from every L sample of the board, not
             from the plot; a session name; the port dropdown and connect
             button (moved here from real_time_graph.py); counters; runtime
             problems go to a status line and the log, never to popups, so a
             car session is never blocked by a dialog.
Description: The "Record" panel. Owns the SerialHandler (reader thread), the
             ClipRecorder and the RecordController, connects them, shows the
             connection and the board's settings, and tells the Last clip card
             the recording state, counters and each clip event. Also passes
             every LIVE_DECIMATE-th L sample to the live IMU view.
"""

# ============================================================================
# Imports
# ============================================================================

import datetime
import html
import logging
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QSettings, Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QGridLayout, QGroupBox,
                             QHBoxLayout, QLabel, QLineEdit, QPushButton, QShortcut,
                             QVBoxLayout, QWidget)

import cnn_config as cfg
from app_styles import AppStyles, set_flag, set_role, set_state
from clip_recorder import ENDED_CLOSED, ENDED_TIMEOUT, Clip, ClipRecorder
from elided_label import ElidedLabel
from live_plot import LivePlot
from record_core import RecordController
from serial_handler import SerialHandler, list_ports
from snapshot_protocol import BootLine, SampleLine
from sounds import Sounds
from status_card import ARMED, IDLE, OK, WARN, StatusCard
from take_store import TakeStore

# ============================================================================
# Constants
# ============================================================================

_LOG = logging.getLogger("record")

# QSettings keys (per-user, remembered between runs).
_SETTINGS_ORG = "EE475Team5"
_SETTINGS_APP = "cnn_record_gui"

# Button words per label.
_LABEL_WORDS = {"POTHOLE": "Pothole", "SPEED_BUMP": "Speed bump", "NONE": "Nothing"}

# The simulated device's "on press, inject" choices: (text, shape). None =
# the shape of the key pressed (P pothole, B speed bump, N nothing).
_SIM_SHAPES = (("shape of the key", None), ("pothole", "POTHOLE"),
               ("speed bump", "SPEED_BUMP"), ("nothing", "NONE"))

# How often the panel checks a stalled clip and redraws the countdown (ms).
_TICK_MS = 100

# ============================================================================
# Functions
# ============================================================================


# FLT codes of model faults (firmware types.h F5-F8): they stop the board's
# inference only (owner, 2026-11-24).
MODEL_FAULT_CODES = ("F5", "F6", "F7", "F8")


def _mmss(seconds: float) -> str:
    """
    @param seconds  A duration.
    @return It as m:ss.
    """
    return "%d:%02d" % divmod(int(seconds), 60)

# ============================================================================
# Classes
# ============================================================================


class RecordWidget(QGroupBox):
    """Connection, session, record keys, counters."""

    take_saved = pyqtSignal(object)       # Path of a clip just written
    take_deleted = pyqtSignal(object)     # Path of a clip Backspace moved to _deleted
    session_changed = pyqtSignal(str)     # A new valid session name
    counts_changed = pyqtSignal(str)      # The top bar's text (session clip counts)
    handler_started = pyqtSignal(object)  # A new SerialHandler, before it starts (request G)

    def __init__(self, store: TakeStore, gui_commit: str, live_plot: LivePlot,
                 card: StatusCard) -> None:
        """
        @param store       Where clips are written.
        @param gui_commit  Commit of this checkout (into every clip).
        @param live_plot   Where every LIVE_DECIMATE-th L sample is drawn.
        @param card        The Last clip card (recording state, counters, clip events).
        """
        super().__init__("Record")
        self.store = store
        self.live_plot = live_plot
        self.card = card
        self.settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self.ctrl = RecordController(store, gui_commit, on_saved=self._on_saved,
                                     on_error=self._on_error)
        self.recorder = ClipRecorder()
        self.sounds = Sounds(self)
        self.handler: Optional[SerialHandler] = None
        self._shortcuts = []                    # The record keys (set_keys_enabled)
        self._ports = []
        self._connected = False        # Port (or simulated device) open right now
        self._conn_message = "Not connected"
        self._boot_info = ""           # Strip text from BOOT / CFG (rich text, escaped)
        self._health_info = ""         # Strip text from HLT
        self._model_fault = ""         # Strip text for a model fault (FLT F5-F8)
        self._l_count = 0              # L samples received on this connection
        self._rate_mark = None         # (monotonic time, _l_count) at the last HLT
        self._l_rate: Optional[float] = None   # L samples per second
        self._long_error_shown = False

        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)

        self.conn_dot = set_role(QLabel("●"), "dot")
        self.conn_label = ElidedLabel("")
        self.strip = set_role(QLabel(""), "strip")
        self.strip.setTextFormat(Qt.RichText)
        self.strip.setWordWrap(True)   # A second line in a narrow window, never cut off
        self.msg_label = set_role(QLabel(""), "error")
        self.msg_label.setWordWrap(True)
        self.msg_label.hide()          # Until the first problem

        grid = QGridLayout()
        grid.setHorizontalSpacing(AppStyles.PANEL_SPACING)
        grid.setVerticalSpacing(AppStyles.PANEL_SPACING)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        # Row 0: port and Refresh
        grid.addWidget(QLabel("Port"), 0, 0)
        self.port_combo = QComboBox()
        grid.addWidget(self.port_combo, 0, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._refresh_ports)
        grid.addWidget(self.refresh_button, 0, 2)

        # Row 1: simulated device, and Connect across the rest of the row
        connect_row = QHBoxLayout()
        self.sim_check = QCheckBox("Simulated device")
        self.sim_check.setToolTip("No hardware: a simulated board, with test controls")
        self.sim_check.toggled.connect(self._on_sim_toggled)
        connect_row.addWidget(self.sim_check)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._toggle_connection)
        connect_row.addWidget(self.connect_button, 1)
        grid.addLayout(connect_row, 1, 0, 1, 3)

        # Rows 2-3: connection line, device info strip
        conn_row = QHBoxLayout()
        conn_row.setSpacing(6)
        conn_row.addWidget(self.conn_dot)
        conn_row.addWidget(self.conn_label, 1)
        grid.addLayout(conn_row, 2, 0, 1, 3)
        grid.addWidget(self.strip, 3, 0, 1, 3)

        # Row 4: session
        grid.addWidget(QLabel("Session"), 4, 0)
        self.session_edit = QLineEdit(self.settings.value(
            "session", datetime.date.today().isoformat() + "_bench"))
        self.session_edit.editingFinished.connect(self._apply_session)
        grid.addWidget(self.session_edit, 4, 1, 1, 2)

        # Record keys: P / B / N, then L and delete. NoFocus, so a click
        # leaves the keyboard keys working and Space never repeats a press.
        key_row = QHBoxLayout()
        self.key_buttons = {}
        self._key_tips = {}
        for key, label in cfg.RECORD_KEYS.items():
            button = set_role(QPushButton("%s  %s" % (key, _LABEL_WORDS.get(label, label))),
                              "record-key")
            button.setFocusPolicy(Qt.NoFocus)
            self._key_tips[label] = ("Records a %s clip: %.0f s before to %.0f s after the press"
                                     % (label, cfg.MANUAL_PRE_S, cfg.MANUAL_POST_S))
            button.clicked.connect(lambda _=False, lab=label: self._press(lab))
            key_row.addWidget(button, 1)
            self.key_buttons[label] = button
        layout.addLayout(key_row)
        long_row = QHBoxLayout()
        self.long_button = set_role(QPushButton(""), "record-key")
        self.long_button.setFocusPolicy(Qt.NoFocus)
        self.long_button.clicked.connect(self._toggle_long)
        self.delete_button = set_role(QPushButton("⌫  Delete last clip"), "record-key")
        self.delete_button.setFocusPolicy(Qt.NoFocus)
        self.delete_button.setToolTip("Moves the last clip saved in this run to the session's "
                                      "%s folder (Backspace)" % cfg.DELETED_DIR_NAME)
        self.delete_button.clicked.connect(self._delete_last)
        long_row.addWidget(self.long_button, 1)
        long_row.addWidget(self.delete_button, 1)
        layout.addLayout(long_row)
        notes = QVBoxLayout()
        notes.setSpacing(AppStyles.NOTE_SPACING)
        notes.addWidget(self._note(
            "Press %s when you see the event coming: one key labels and records it. Each press "
            "saves %.0f s before to %.0f s after the press as one clip, whatever it holds."
            % (" / ".join(cfg.RECORD_KEYS), cfg.MANUAL_PRE_S, cfg.MANUAL_POST_S)))
        notes.addWidget(self._note(
            "%s starts and stops a long NONE recording of normal road. Backspace deletes the last "
            "clip. You review every clip in Collected clips." % cfg.LONG_KEY))
        layout.addLayout(notes)

        # Simulated device test controls (shown with Simulated device only)
        self.sim_panel = QWidget()
        sim_layout = QVBoxLayout(self.sim_panel)
        sim_layout.setContentsMargins(0, 0, 0, 0)
        sim_layout.setSpacing(AppStyles.NOTE_SPACING)
        inject_row = QHBoxLayout()
        inject_row.addWidget(QLabel("Sim: on press, inject"))
        self.sim_shape_combo = QComboBox()
        for text, shape in _SIM_SHAPES:
            self.sim_shape_combo.addItem(text, shape)
        inject_row.addWidget(self.sim_shape_combo, 1)
        inject_row.addWidget(QLabel("after"))
        self.sim_delay = QDoubleSpinBox()
        self.sim_delay.setRange(0.0, cfg.MANUAL_POST_S - 0.5)
        self.sim_delay.setSingleStep(0.1)
        self.sim_delay.setDecimals(1)
        self.sim_delay.setSuffix(" s")
        self.sim_delay.setKeyboardTracking(False)
        self.sim_delay.setValue(cfg.SIM_INJECT_DELAY_S)
        # Leave the box after typing, so P / B / N record again.
        self.sim_delay.editingFinished.connect(self.sim_delay.clearFocus)
        inject_row.addWidget(self.sim_delay)
        sim_layout.addLayout(inject_row)
        fault_row = QHBoxLayout()
        self.sim_buttons = []
        for text, cmd, tip in (("Gap", "gap", "Lose %d L lines (a jump in n)" % cfg.SIM_GAP_SAMPLES),
                               ("Reset", "reset", "The board resets: BOOT, n back to 0"),
                               ("ok=0", "ok0", "Send %d L lines with ok=0" % cfg.SIM_OK0_SAMPLES),
                               ("Disconnect", "disconnect",
                                "USB drops for %.0f s, then the board reboots"
                                % cfg.SIM_DISCONNECT_S)):
            button = QPushButton(text)
            button.setFocusPolicy(Qt.NoFocus)
            button.setToolTip(tip)
            button.clicked.connect(lambda _=False, c=cmd: self._sim_command(c))
            fault_row.addWidget(button, 1)
            self.sim_buttons.append(button)
        sim_layout.addLayout(fault_row)
        self.sim_panel.hide()
        layout.addWidget(self.sim_panel)

        layout.addWidget(self.msg_label)
        layout.addWidget(self._note("Firmware: firmware-bench 0.1.5-bench (it streams every "
                                    "sample as an L line)."))

        # Keys: active while this window is active. A focused text box keeps
        # its own typing. Auto-repeat off: holding a key is one press.
        for key, label in cfg.RECORD_KEYS.items():
            self._shortcut(key, lambda lab=label: self._press(lab))
        self._shortcut(cfg.LONG_KEY, self._toggle_long)
        self._shortcut(cfg.DELETE_LAST_KEY, self._delete_last)

        # Stalled clips and the countdown.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(_TICK_MS)

        self._refresh_ports()
        self._apply_session()
        self._set_conn(IDLE, "Not connected")
        self._show_strip()
        self._refresh()

    # ------------------------------------------------------------ UI helpers

    def _shortcut(self, key: str, slot) -> None:
        """
        @param key   Key text for QKeySequence, e.g. "P" or "Backspace".
        @param slot  Called once per press (auto-repeat off).
        """
        sc = QShortcut(QKeySequence(key), self)
        sc.setAutoRepeat(False)
        sc.activated.connect(slot)
        self._shortcuts.append(sc)

    def set_keys_enabled(self, enabled: bool) -> None:
        """
        P7 (request G): the record keys (P, B, N, L, Backspace) work only while
        the Record tab is open. The window calls this when the tab changes.
        @param enabled  True on the Record tab.
        """
        for sc in self._shortcuts:
            sc.setEnabled(enabled)

    @staticmethod
    def _note(text: str) -> QLabel:
        """
        @param text  Note text.
        @return A gray italic note that wraps.
        """
        note = set_role(QLabel(text), "note")
        note.setWordWrap(True)
        return note

    def _set_conn(self, state: str, message: str) -> None:
        """
        @param state    Dot color: OK (open), WARN (lost, retrying) or IDLE.
        @param message  The connection message.
        """
        self._conn_message = message
        set_state(self.conn_dot, state)
        self.conn_label.setText(message)

    def _show_strip(self) -> None:
        """Device info strip: the BOOT settings, then the HLT rates and drops."""
        parts = [text for text in (self._boot_info, self._health_info, self._model_fault)
                 if text]
        self.strip.setText(" | ".join(parts) if parts else "No device info yet.")

    @staticmethod
    def _warn_html(text: str) -> str:
        """
        @param text  Plain text.
        @return The text, escaped, in the warning color (for the strip).
        """
        return '<span style="color:%s">%s</span>' % (AppStyles.ACCENT_WARN, html.escape(text))

    def _block_reason(self) -> str:
        """
        @return Why a press cannot start a recording now ("" if it can).
        """
        if not self._connected:
            return "Connect a board (or the simulated device) before recording"
        if not self.ctrl.session:
            return "Enter a valid session name before recording"
        return ""

    def _refresh(self) -> None:
        """Redraws the card's base state, the counters and the buttons."""
        now = time.monotonic()
        rec = self.recorder
        reason = self._block_reason()
        for label, button in self.key_buttons.items():
            button.setEnabled(not reason and not rec.busy)
            button.setToolTip(reason or self._key_tips[label])
            set_flag(button, "armed", rec.recording and rec.label == label)
        if rec.long_active:
            self.long_button.setText("%s  Stop long NONE  %s"
                                     % (cfg.LONG_KEY, _mmss(rec.long_elapsed_s(now))))
        else:
            self.long_button.setText("%s  Long NONE" % cfg.LONG_KEY)
        self.long_button.setEnabled(rec.long_active or (not reason and not rec.recording))
        set_flag(self.long_button, "armed", rec.long_active)
        self.delete_button.setEnabled(self.ctrl.last_saved is not None)
        self.session_edit.setEnabled(not rec.busy)   # Fixed while a recording uses it
        running = self.handler is not None and self.handler.isRunning()
        self.sim_panel.setEnabled(running and self.handler.simulated)

        if rec.long_active:
            self.card.set_base(ARMED, "LONG RECORDING",
                               "NONE · %s · %d rows · %s stops and saves it · "
                               "%s are ignored" % (_mmss(rec.long_elapsed_s(now)), rec.long_rows(),
                                                   cfg.LONG_KEY, " / ".join(cfg.RECORD_KEYS)))
        elif rec.recording:
            left = rec.remaining_s(now)
            sub = "%.1f s left" % left if left > 0 else "Saving when the last samples arrive"
            self.card.set_base(ARMED, "RECORDING %s" % rec.label, sub)
        elif not self._connected:
            self.card.set_base(IDLE, "NOT CONNECTED", self._conn_message if running else
                               "Pick a port or Simulated device, then Connect")
        else:
            self.card.set_base(IDLE, "READY", "  ·  ".join(
                ["%s %s" % (k, _LABEL_WORDS.get(v, v).lower()) for k, v in cfg.RECORD_KEYS.items()]
                + ["%s long NONE" % cfg.LONG_KEY]), keeps_take=True)
        c = self.ctrl.counters
        self.card.set_counters(
            "this run: saved %d | not saved %d | ignored presses %d | deleted %d\n"
            "L lines/s %s | malformed L %d"
            % (c.saved, c.save_errors, c.ignored, c.deleted,
               "-" if self._l_rate is None else "%.0f" % self._l_rate,
               self.handler.malformed_l if self.handler is not None else 0))

    def refresh_counts(self) -> None:
        """Recounts the session's clips from disk for the top bar (also called
        by the main window once its top bar is connected)."""
        self.counts_changed.emit(self.ctrl.session_counts().text(self.ctrl.session))

    # ----------------------------------------------------------- user actions

    def _refresh_ports(self) -> None:
        """Re-lists serial ports, keeping or restoring the last choice."""
        current = self.port_combo.currentData() or self.settings.value("last_port", "")
        try:
            self._ports = list_ports()
        except Exception as exc:  # pyserial raises different errors per OS
            self._ports = []
            self._on_error("Cannot list ports: %r" % exc)
        self.port_combo.clear()
        for p in self._ports:
            self.port_combo.addItem(p.label(), p.device)
        k = self.port_combo.findData(current)
        if k >= 0:
            self.port_combo.setCurrentIndex(k)

    def _toggle_connection(self) -> None:
        """Connect / Disconnect button."""
        if self.handler is not None and self.handler.isRunning():
            self.handler.stop()  # _on_reader_finished saves open recordings and resets buttons
            return
        if self.handler is not None:
            self.handler.deleteLater()  # The previous, finished reader
            self.handler = None
        simulated = self.sim_check.isChecked()
        port = None
        if not simulated:
            device = self.port_combo.currentData()
            port = next((p for p in self._ports if p.device == device), None)
            if port is None:
                self._on_error("Choose a port (or Simulated device) first")
                return
            self.settings.setValue("last_port", port.device)
        self.handler = SerialHandler(self)
        self.handler_started.emit(self.handler)     # The Live Inference tab listens too
        self.handler.boot_received.connect(self._on_boot)
        self.handler.config_received.connect(self._on_config)
        self.handler.health_received.connect(self._on_health)
        self.handler.fault_received.connect(self._on_fault)
        self.handler.samples_received.connect(self._on_samples)
        self.handler.connection_changed.connect(self._on_connection)
        self.handler.reconnected.connect(self._on_reconnected)
        self.handler.finished.connect(self._on_reader_finished)
        self.handler.configure(port, simulated)
        self.live_plot.reset()
        self.card.clear_fault()          # A fault stays up until the next Connect
        self.card.set_simulated(simulated)
        self.ctrl.on_new_connection()    # The last connection's BOOT may not apply now
        self.recorder.stream_restarted()
        self._boot_info = ""
        self._health_info = ""
        self._model_fault = ""
        self._show_strip()
        self._l_count = 0
        self._rate_mark = None
        self._l_rate = None
        self.handler.start()
        _LOG.info("Connect: %s", "simulated device" if simulated else port.label())
        self.connect_button.setText("Disconnect")
        self.sim_check.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.port_combo.setEnabled(False)
        self._refresh()

    def _on_sim_toggled(self, on: bool) -> None:
        """
        @param on  Simulated device checkbox: the port list or the sim controls.
        """
        self.port_combo.setEnabled(not on)
        self.sim_panel.setVisible(on)

    def _apply_session(self) -> None:
        """Takes the session name from the text box if it is valid."""
        # Leave the text box, so P / B / N record instead of typing here.
        self.session_edit.clearFocus()
        name = self.session_edit.text().strip()
        try:
            self.store.validate_session(name)
        except ValueError as exc:
            self._on_error(str(exc))
            self.session_edit.setText(self.ctrl.session)  # Back to the last good one
            return
        if name != self.ctrl.session:
            self.ctrl.session = name
            self.ctrl.last_saved = None     # Backspace never reaches into another session
            self.settings.setValue("session", name)
            _LOG.info("Session: %s", name)
            self.session_changed.emit(name)
            self.card.clear_recent()
            self.refresh_counts()
            self._refresh()

    def _press(self, label: str) -> None:
        """
        P / B / N (key or button): starts a clip with this label. Ignored
        (and counted) during a clip (O7) or a long recording (B3).
        @param label  The key's label.
        """
        reason = self._block_reason()
        if reason:
            self._on_error(reason)
            return
        why = self.recorder.press(label, int(time.time() * 1000), time.monotonic())
        if why:
            self.ctrl.counters.ignored += 1
            _LOG.info("Press ignored: %s", why)
            self._refresh()
            return
        self.sounds.press()
        _LOG.info("Press %s", label)
        if self.handler is not None and self.handler.simulated:
            shape = self.sim_shape_combo.currentData() or label
            if shape != "NONE":
                self.handler.sim_command("event", shape, self.sim_delay.value())
        self._refresh()

    def _toggle_long(self) -> None:
        """L (key or button): starts or stops a long NONE recording."""
        epoch_ms = int(time.time() * 1000)
        if self.recorder.long_active:
            clip = self.recorder.stop_long(epoch_ms)
            _LOG.info("Long recording stopped: %d rows", clip.facts.rows)
            self._save(clip)
            return
        if self.recorder.recording:
            self.ctrl.counters.ignored += 1
            _LOG.info("L ignored: a clip is recording")
            self._refresh()
            return
        reason = self._block_reason()
        if reason:
            self._on_error(reason)
            return
        body = self.store.session_dir(self.ctrl.session) / (
            "%s%d%s" % (cfg.LONG_PARTIAL_PREFIX, epoch_ms, cfg.LONG_PARTIAL_SUFFIX))
        why = self.recorder.start_long(epoch_ms, time.monotonic(), body)
        if why:
            self._on_error(why[0].upper() + why[1:])
            return
        self._long_error_shown = False
        self.sounds.press()
        _LOG.info("Long recording started (rows go to %s until it stops)", body.name)
        self._refresh()

    def _delete_last(self) -> None:
        """Backspace (or its button): moves the last clip saved in this run to _deleted."""
        if self.ctrl.last_saved is None:
            self.card.show_take(IDLE, "NO CLIP TO DELETE",
                                "Backspace deletes the last clip saved in this run, once")
            return
        moved = self.ctrl.delete_last()
        if moved is None:
            return                       # The error is on the message line
        old, dest = moved
        _LOG.info("Deleted %s (moved to %s)", old.name, dest)
        self.card.rename_recent(old.stem, None)
        self.card.show_take(IDLE, old.stem, "Deleted: moved to %s/%s"
                            % (cfg.DELETED_DIR_NAME, dest.name))
        self.take_deleted.emit(old)
        self.refresh_counts()
        self._refresh()

    def _sim_command(self, cmd: str) -> None:
        """
        @param cmd  A simulated-device test control: gap, reset, ok0 or disconnect.
        """
        if self.handler is not None and self.handler.isRunning():
            self.handler.sim_command(cmd)
            _LOG.info("Simulated device: %s", cmd)

    def _save(self, clip: Clip) -> None:
        """
        Saves a finished clip (every one, whatever it holds).
        @param clip  From the ClipRecorder.
        """
        if self.ctrl.save_clip(clip) is None:
            self.card.show_take(WARN, "NOT SAVED", "The clip could not be written: see the "
                                "message line")
        self.refresh_counts()
        self._refresh()

    def take_moved(self, old: Path, new: Optional[Path]) -> None:
        """
        The clip list relabeled or deleted a clip.
        @param old  Old path.
        @param new  New path, or None if deleted.
        """
        self.ctrl.take_moved(old, new)
        _LOG.info("Clip %s -> %s", old.name, new.name if new else "deleted")
        self.card.rename_recent(old.stem, new.stem if new else None)
        self.refresh_counts()
        self._refresh()

    def shutdown(self) -> None:
        """Window closing: saves what open recordings collected, then stops
        the reader thread."""
        self.timer.stop()
        for clip in self.recorder.finish_all(int(time.time() * 1000)):
            self._save(clip)
        if self.handler is not None and self.handler.isRunning():
            self.handler.stop()

    # --------------------------------------------------------- reader signals

    def _on_boot(self, boot) -> None:
        """
        @param boot  A BootLine: the board (re)booted.
        """
        self.ctrl.on_boot(boot)
        self.recorder.stream_restarted()
        _LOG.info("BOOT: %s", boot.raw)
        self._apply_settings(boot, from_cfg=False)

    def _on_config(self, line) -> None:
        """
        @param line  A ConfigLine: the board's settings, repeated every
                     CFG_PERIOD_S. Used only while this connection has none
                     (its BOOT was missed). Not a reboot: nothing is cleared.
        """
        boot = line.as_boot()
        if self.ctrl.on_settings(boot):
            _LOG.info("Settings from CFG (no BOOT seen on this connection): %s", line.raw)
            self._apply_settings(boot, from_cfg=True)

    def _apply_settings(self, boot: BootLine, from_cfg: bool) -> None:
        """
        Shows the board's settings in the strip and sizes the clip buffer by
        its rate.
        @param boot      A BOOT line, or a CFG's as_boot().
        @param from_cfg  The settings came from a CFG line (BOOT was missed).
        """
        s = {k: html.escape(v) for k, v in boot.settings.items()}
        parts = ["fw %s%s" % (s.get("fw", "?"), " (settings from CFG)" if from_cfg else ""),
                 "%s Hz, ±%s g, ±%s dps" % (s.get("odr", "?"), s.get("acc_fs", "?"),
                                                    s.get("gyr_fs", "?")),
                 "HPF %s Hz, settle %s ms" % (s.get("hpf_hz", "?"), s.get("settle_ms", "?")),
                 "vert %s, roll %s, pitch %s" % (s.get("vert", "?"), s.get("roll", "?"),
                                                 s.get("pitch", "?"))]
        if boot.settings.get("rec") == "1" or ("log" in boot.settings
                                               and boot.settings.get("log") != "1"):
            parts.append(self._warn_html("this firmware sends no L lines: flash "
                                         "firmware-bench 0.1.5-bench"))
        self._boot_info = " | ".join(parts)
        self._show_strip()
        self.card.set_simulated(boot.settings.get("fw", "").startswith("sim-"))
        try:
            self.recorder.set_rate(int(boot.settings.get("odr", cfg.DEFAULT_RATE_HZ)))
        except ValueError:
            pass   # Keeps the default rate
        self._refresh()

    def _on_samples(self, batch: list) -> None:
        """
        A batch of L samples: every LIVE_DECIMATE-th to the live view, all of
        them to the clip recorder.
        @param batch  LogSample list, oldest first.
        """
        self._l_count += len(batch)
        for s in batch:
            if s.n % cfg.LIVE_DECIMATE == 0:
                # Chip axes (gyro bias in), then z_hp.
                self.live_plot.add_sample(SampleLine(s.n, s.t_us, s.raw + s.model[:1]))
        clip = self.recorder.feed(batch)
        if self.recorder.long_error and not self._long_error_shown:
            self._long_error_shown = True
            self._on_error("Long recording: rows could not be written: %s"
                           % self.recorder.long_error)
        if clip is not None:
            self._save(clip)

    def _on_health(self, hlt) -> None:
        """
        @param hlt  A HealthLine.
        """
        now = time.monotonic()
        # L lines per second since the last HLT (the live view is decimated).
        if self._rate_mark is not None and now - self._rate_mark[0] > 0.2:
            self._l_rate = (self._l_count - self._rate_mark[1]) / (now - self._rate_mark[0])
        self._rate_mark = (now, self._l_count)
        counts = [(name, hlt.fields[key]) for name, key in
                  (("sample q", "sample_q_drops"), ("out q", "out_q_drops"),
                   ("serial", "serial_drops")) if key in hlt.fields]
        drop_text = "drops: " + ", ".join("%s %d" % nc for nc in counts)
        if any(n for _name, n in counts):
            drop_text = self._warn_html(drop_text)
        self._health_info = "%s L lines/s | %s" % (
            "-" if self._l_rate is None else "%.0f" % self._l_rate, drop_text)
        self._show_strip()
        self._refresh()

    def _on_fault(self, flt) -> None:
        """
        @param flt  A FaultLine.
        """
        if flt.code in MODEL_FAULT_CODES:
            # The board's model stopped; sampling and the L stream go on, so
            # clips still record. Repeated every second: logged once.
            text = self._warn_html("inference stopped: %s %s (recording still works)"
                                   % (html.escape(flt.code), html.escape(flt.name)))
            if text != self._model_fault:
                self._model_fault = text
                _LOG.warning("FLT %s %s: the board's model stopped; streaming and recording "
                             "go on", flt.code, flt.name)
                self._show_strip()
            return
        self.card.show_fault("FAULT %s" % flt.code, "DEVICE FAULT: %s %s · stays until the "
                             "next Connect" % (flt.code, flt.name))
        _LOG.warning("FLT %s %s", flt.code, flt.name)

    def _on_connection(self, ok: bool, message: str) -> None:
        """
        A recording in progress goes on through a lost port: it is saved
        when its samples are in, or after its grace time with what arrived.
        @param ok       Connected? (The message says it; kept for the log.)
        @param message  What happened.
        """
        self._connected = ok
        self._set_conn(OK if ok else WARN, message)
        self.live_plot.set_connected(ok)
        _LOG.info("Connection (%s): %s", "up" if ok else "down", message)
        self._refresh()

    def _on_reader_finished(self) -> None:
        """The reader thread ended (Disconnect, or an error): open recordings
        are saved with what they collected, and the buttons go back to Connect."""
        self.live_plot.set_connected(False)
        self.connect_button.setText("Connect")
        self.sim_check.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.port_combo.setEnabled(not self.sim_check.isChecked())
        self._connected = False
        set_state(self.conn_dot, IDLE)
        for clip in self.recorder.finish_all(int(time.time() * 1000)):
            _LOG.info("Disconnected during a recording: saving what arrived")
            self._save(clip)
        self._refresh()

    def _on_reconnected(self) -> None:
        """The port came back: the board may have reset or been reflashed."""
        self.ctrl.on_reconnect()
        self.recorder.stream_restarted()
        self._boot_info = ("Device settings: waiting for BOOT or CFG (cleared on reconnect; "
                           "CFG comes every %.0f s)" % cfg.CFG_PERIOD_S)
        self._model_fault = ""
        self._show_strip()
        _LOG.info("Reconnected")
        self._refresh()

    # ------------------------------------------------------ controller hooks

    def _on_saved(self, path: Path, clip: Clip) -> None:
        """
        @param path  The clip just written.
        @param clip  What it holds.
        """
        f = clip.facts
        sub = "%d rows · missing %d · resets %d · ok=0 %d" % (
            f.rows, f.missing, f.resets, f.ok0)
        if clip.ended_by == ENDED_TIMEOUT:
            sub += " · the stream stopped: saved what arrived"
        elif clip.ended_by == ENDED_CLOSED:
            sub += " · saved on disconnect"
        self.sounds.saved()
        self.card.add_recent(path.stem)
        self.card.show_take(OK, path.stem, sub)
        _LOG.info("Saved %s/%s (%s; %s)", self.ctrl.session, path.name, clip.ended_by, sub)
        self.take_saved.emit(path)

    def _on_error(self, message: str) -> None:
        """
        @param message  Shown on the status line and logged; never a popup.
        """
        self.msg_label.setText(message)
        self.msg_label.show()
        _LOG.error(message)

    def _tick(self) -> None:
        """Every _TICK_MS: a clip whose stream stopped is saved after its
        grace time; the countdown and the long recording's time redraw."""
        clip = self.recorder.tick(time.monotonic())
        if clip is not None:
            _LOG.info("Clip timed out waiting for samples: saving what arrived")
            self._save(clip)
        elif self.recorder.busy:
            self._refresh()
