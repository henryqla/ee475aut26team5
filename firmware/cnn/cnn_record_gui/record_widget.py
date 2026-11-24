"""
File:        record_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.2
Change:      0.3.2 (owner, 2026-11-23): no weak takes. Every manual take,
             however weak the event, gets the green "Manual take" card and
             goes into Collected takes; the counters drop "weak".
             0.3.1: the board's settings can come from a CFG line (BOOT's
             settings, repeated every CFG_PERIOD_S) when BOOT was missed,
             without BOOT's reboot handling. Record and Start log stay off
             until the connection has settings, so a press can no longer
             give a raw file with nothing to cut it by. Connect forgets the
             previous connection's BOOT. In the log build EVTs are no longer
             paired with snapshots (none come), so they no longer log "had no
             valid snapshot".
             0.3.0 (drive-log-manual-record-handoff.md v0.2, request 5;
             one-press-manual-takes-handoff.md v0.1, request 8): the log
             build. The panel tells the build from BOOT (log=1 or rec=1), or
             from the line types until BOOT arrives: the log build enables
             Start log and Record and disables Arm, the record build the
             reverse, and each disabled control says why. Start log / Stop
             log writes the drive log (in the reader thread) with a status
             line; Record (or R) saves one finished take from the 3 s before
             and after the press (manual_record.py), shown on the Last take
             card and in Collected takes. In the log build P / B / F write a
             MARK (if the log is running) and record a take with that key's
             label (owner, 2026-11-22); in the record build they relabel the
             latest take as before. The live view draws every LIVE_DECIMATE-th
             L sample. The simulated device can run as either build.
Adapted from: Python/record_data_widget.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             QGroupBox with label input, a record button and notes. Changed
             (handoff, "GUI record path changes"): no timer and no capture
             from the plot (the framework kept one point per 20 ms tick);
             instead Arm saves the firmware's next valid snapshot, "keep
             armed" saves every one; a fixed label dropdown; a session name;
             the port dropdown and connect button (moved here from
             real_time_graph.py); counters; P / B / F quick labels; and
             runtime problems go to a status line and the log, never to
             popups, so a car session is never blocked by a dialog.
             Restyled (request 7): rows regrouped, styles from app_styles.py;
             the status lines became the Last take card (status_card.py), a
             connection line, the device info strip and an error line.
Description: The "Record" panel. Owns the SerialHandler (reader thread) and
             the RecordController (record_core), connects one to the other,
             shows the connection and the device's settings, and tells the
             Last take card the arm state, counters and each take event.
             Also passes the reader's S lines to the live IMU view (display
             only; they never reach the RecordController).
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
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QShortcut, QVBoxLayout)

import cnn_config as cfg
from app_styles import AppStyles, set_flag, set_role, set_state
from elided_label import ElidedLabel
from live_plot import LivePlot
from manual_record import NO_TAKE, ManualRecorder, save_capture
from record_core import Claim, RecordController
from serial_handler import SerialHandler, list_ports
from snapshot_plot import SnapshotPlot
from snapshot_protocol import BootLine, SampleLine
from status_card import ARMED, IDLE, OK, WARN, StatusCard
from take_store import TakeStore, load_take, parse_take_name

# ============================================================================
# Constants
# ============================================================================

_LOG = logging.getLogger("record")

# QSettings keys (per-user, remembered between runs).
_SETTINGS_ORG = "EE475Team5"
_SETTINGS_APP = "cnn_record_gui"

# Which firmware build is connected (RecordWidget._build).
_BUILD_UNKNOWN = "unknown"   # Nothing seen yet on this connection
_BUILD_LOG = "log"           # LOG_MODE 1: L lines, drive log and Record
_BUILD_RECORD = "record"     # RECORD_MODE 1 (or S lines seen): snapshots and Arm
_BUILD_PRODUCT = "product"   # Both flags 0: S lines, no snapshots

# The simulated device's builds, in the dropdown's order.
_SIM_BUILDS = (("record build", "record"), ("log build", "log"))

# ============================================================================
# Classes
# ============================================================================


class RecordWidget(QGroupBox):
    """Connection, session, label, Arm, counters."""

    take_saved = pyqtSignal(object)       # Path of a take just written or relabeled
    session_changed = pyqtSignal(str)     # A new valid session name

    def __init__(self, store: TakeStore, gui_commit: str, last_plot: SnapshotPlot,
                 live_plot: LivePlot, card: StatusCard) -> None:
        """
        @param store       Where takes are written.
        @param gui_commit  Commit of this checkout (into every take).
        @param last_plot   Where each arriving snapshot is drawn.
        @param live_plot   Where the S lines (live IMU samples) are drawn.
        @param card        The Last take card (arm state, counters, take events).
        """
        super().__init__("Record")
        self.store = store
        self.last_plot = last_plot
        self.live_plot = live_plot
        self.card = card
        self.settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self.ctrl = RecordController(store, gui_commit, on_saved=self._on_saved,
                                     on_error=self._on_error, on_info=self._on_info)
        self.handler: Optional[SerialHandler] = None
        self._ports = []
        self._last_drops = None  # Last device snap_drops logged
        # Log build (request 5, request 8)
        self.recorder = ManualRecorder()
        self._build = _BUILD_UNKNOWN
        self._logging = False           # A drive log is running (or starting)
        self._log_text = ""             # The log status line (rich text)
        self._port_name = ""            # For the drive log header
        self._l_count = 0               # L samples received on this connection
        self._rate_mark = None          # (monotonic time, _l_count) at the last HLT
        self._l_rate: Optional[float] = None   # L samples per second

        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)

        # Connection line (dot and message), device info strip and error
        # line; arm state, counters and take events go to the card.
        self.conn_dot = set_role(QLabel("\u25cf"), "dot")
        self.conn_label = ElidedLabel("")
        self.strip = set_role(QLabel(""), "strip")
        self.strip.setTextFormat(Qt.RichText)
        self.strip.setWordWrap(True)   # A second line in a narrow window, never cut off
        self.msg_label = set_role(QLabel(""), "error")
        self.msg_label.setWordWrap(True)
        self.msg_label.hide()          # Until the first problem
        self._connected = False        # Port (or simulated device) open right now
        self._conn_message = "Not connected"
        self._boot_info = ""           # Strip text from BOOT (rich text, escaped)
        self._health_info = ""         # Strip text from HLT

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

        # Row 1: simulated device and its build, and Connect across the rest
        # of the row
        connect_row = QHBoxLayout()
        self.sim_check = QCheckBox("Simulated device")
        self.sim_check.setToolTip("No hardware: a simulated board, as the record or log build")
        self.sim_build_combo = QComboBox()
        for text, build in _SIM_BUILDS:
            self.sim_build_combo.addItem(text, build)
        self.sim_build_combo.setEnabled(False)
        self.sim_check.toggled.connect(self._on_sim_toggled)
        connect_row.addWidget(self.sim_check)
        connect_row.addWidget(self.sim_build_combo)
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

        # Row 5: label
        grid.addWidget(QLabel("Label"), 5, 0)
        self.label_combo = QComboBox()
        self.label_combo.addItems(cfg.LABELS)
        self.label_combo.setCurrentText(cfg.UNLABELED)
        self.label_combo.currentTextChanged.connect(self._apply_label)
        grid.addWidget(self.label_combo, 5, 1, 1, 2)

        # Arm across the row, Keep armed beside it; each note sits under
        # what it explains.
        arm_row = QHBoxLayout()
        self.arm_button = QPushButton("Arm")
        self.arm_button.clicked.connect(self._toggle_arm)
        self.keep_check = QCheckBox("Keep armed")
        self.keep_check.toggled.connect(self._apply_keep_armed)
        arm_row.addWidget(self.arm_button, 1)
        arm_row.addWidget(self.keep_check)
        layout.addLayout(arm_row)
        notes = QVBoxLayout()
        notes.setSpacing(AppStyles.NOTE_SPACING)
        notes.addWidget(self._note("Arm saves the next valid snapshot from the device under "
                                   "the label, then disarms. Keep armed saves every one."))
        notes.addWidget(self._note(
            "Car sessions: Keep armed with label UNLABELED; press %s to label the latest take."
            % ", ".join("%s = %s" % kv for kv in cfg.QUICK_LABEL_KEYS.items())))
        layout.addLayout(notes)

        # Log build (request 5, request 8): Start log and Record side by
        # side, the log status line, then the note.
        log_row = QHBoxLayout()
        self.log_button = QPushButton("Start log")
        self.log_button.clicked.connect(self._toggle_log)
        self.record_button = QPushButton("Record (%s)" % cfg.MANUAL_RECORD_KEY)
        self.record_button.clicked.connect(lambda: self._manual_record(self.ctrl.label))
        log_row.addWidget(self.log_button, 1)
        log_row.addWidget(self.record_button, 1)
        layout.addLayout(log_row)
        self.log_label = set_role(QLabel(""), "info")
        self.log_label.setTextFormat(Qt.RichText)
        self.log_label.setWordWrap(True)
        self.log_label.hide()          # Until a log runs
        layout.addWidget(self.log_label)
        layout.addWidget(self._note(
            "Log build: Start log saves every line to the session's %s folder. Record (%s) "
            "saves one take from the %.0f s before and %.0f s after the press, under the "
            "label. %s mark the event in the log and record a take with that label."
            % (cfg.DRIVES_DIR_NAME, cfg.MANUAL_RECORD_KEY, cfg.MANUAL_PRE_S, cfg.MANUAL_POST_S,
               " / ".join(cfg.QUICK_LABEL_KEYS))))

        layout.addWidget(self.msg_label)
        layout.addWidget(self._note("Firmware: firmware-bench built with RECORD_MODE 1 (Arm) "
                                    "or LOG_MODE 1 (Start log, Record)."))

        # Keys: active while this panel's window is active and the panel is
        # visible. A focused text box keeps its own typing. P / B / F label
        # (record build) or mark and record (log build); R records.
        for key, label in cfg.QUICK_LABEL_KEYS.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(lambda lab=label: self._label_key(lab))
        sc = QShortcut(QKeySequence(cfg.MANUAL_RECORD_KEY), self)
        sc.activated.connect(lambda: self._manual_record(self.ctrl.label))

        # Timeouts for EVT pairing, and display refresh.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(250)

        self._refresh_ports()
        self._apply_session()
        self._set_conn(IDLE, "Not connected")
        self._show_strip()
        self._refresh()

    # ------------------------------------------------------------ UI helpers

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
        parts = [text for text in (self._boot_info, self._health_info) if text]
        self.strip.setText(" | ".join(parts) if parts else "No device info yet.")

    @staticmethod
    def _warn_html(text: str) -> str:
        """
        @param text  Plain text.
        @return The text, escaped, in the warning color (for the strip).
        """
        return '<span style="color:%s">%s</span>' % (AppStyles.ACCENT_WARN, html.escape(text))

    @staticmethod
    def _take_summary(path: Path) -> str:
        """
        @param path  A take just written.
        @return Its card subline: seq, rule class, rows and time received.
        """
        try:
            header, _values = load_take(path)
        except (OSError, ValueError):
            return path.name
        # Snapshot takes have pc_received; takes cut from L lines pc_time_est.
        when = header.get("pc_received") or header.get("pc_time_est", "")
        try:
            when = datetime.datetime.fromisoformat(when).strftime("%H:%M:%S")
        except ValueError:
            pass  # Shown as written
        return " \u00b7 ".join(("seq %s" % header.get("seq", "?"),
                                 "rule %s" % header.get("rule_class", "?"),
                                 "%s rows" % header.get("n_rows", "?"), when))

    def _settings_wait_reason(self, action: str) -> str:
        """
        @param action  "Record" or "Start log".
        @return Why action must wait for the board's settings ("" if they are
                known). Takes are cut with BOOT's thresholds, rate and fw.
        """
        if self.ctrl.boot is not None:
            return ""
        return ("%s: waiting for the board's settings (its BOOT line, or the CFG line it "
                "repeats every %.0f s; firmware older than the CFG line needs a reset)"
                % (action, cfg.CFG_PERIOD_S))

    def _record_block_reason(self) -> str:
        """
        @return Why Record cannot start now ("" if it can).
        """
        if not self._connected:
            return "Record needs a connected board (Connect first)"
        if self._build == _BUILD_UNKNOWN:
            return "Record: waiting for the board's first lines"
        if self._build != _BUILD_LOG:
            return "Record needs the log build (LOG_MODE 1)"
        if not self.ctrl.session:
            return "Enter a valid session name before recording"
        return self._settings_wait_reason("Record")

    def _log_block_reason(self) -> str:
        """
        @return Why Start log cannot start a log now ("" if it can).
        """
        if self.handler is None or not self.handler.isRunning():
            return "Start log needs a connected board (Connect first)"
        if self._build == _BUILD_UNKNOWN:
            return "Start log: waiting for the board's first lines"
        if self._build != _BUILD_LOG:
            return "Start log needs the log build (LOG_MODE 1)"
        if not self.ctrl.session:
            return "Enter a valid session name before logging"
        return self._settings_wait_reason("Start log")

    def _refresh(self) -> None:
        """Redraws the card's base state, the counters and the buttons."""
        c = self.ctrl.counters
        label = self.ctrl.label
        running = self.handler is not None and self.handler.isRunning()
        log_build = self._build == _BUILD_LOG
        self._refresh_log_controls()
        if not self._connected:
            self.card.set_base(IDLE, "NOT CONNECTED", self._conn_message if running else
                               "Pick a port or Simulated device, then Connect")
        elif self.recorder.recording:
            self.card.set_base(ARMED, "RECORDING", "%.1f s left \u00b7 label %s"
                               % (self.recorder.remaining_s(), self.recorder.label))
        elif log_build and self._logging:
            self.card.set_base(ARMED, "LOGGING", "Press %s, or %s, to record a take"
                               % (cfg.MANUAL_RECORD_KEY, " / ".join(cfg.QUICK_LABEL_KEYS)),
                               keeps_take=True)
        elif log_build:
            self.card.set_base(IDLE, "LOG BUILD", "Start log saves the drive \u00b7 %s, or %s, "
                               "records a take" % (cfg.MANUAL_RECORD_KEY,
                                                   " / ".join(cfg.QUICK_LABEL_KEYS)),
                               keeps_take=True)
        elif self.ctrl.keep_armed:
            self.card.set_base(ARMED, "KEEP ARMED",
                               "Waiting for a snapshot \u00b7 label %s" % label)
        elif self.ctrl.armed:
            self.card.set_base(ARMED, "ARMED", "Waiting for a snapshot \u00b7 label %s" % label)
        else:
            self.card.set_base(IDLE, "READY", "Snapshots are shown, not saved \u00b7 press Arm "
                               "to save the next one", keeps_take=True)
        self.arm_button.setText("Cancel arm" if self.ctrl.armed else "Arm")
        self.arm_button.setEnabled(not self.ctrl.keep_armed and not log_build)
        self.keep_check.setEnabled(not log_build)
        no_arm = "Arm needs the record build (RECORD_MODE 1); this is the log build" \
            if log_build else ""
        self.arm_button.setToolTip(no_arm)
        self.keep_check.setToolTip(no_arm)
        set_flag(self.arm_button, "armed", self.ctrl.armed or self.ctrl.keep_armed)
        if log_build:
            self.card.set_counters(
                "manual takes %d | no take %d | not saved %d | marks %d\n"
                "L lines/s %s | malformed L %d"
                % (c.manual_takes, c.manual_no_take, c.manual_not_saved, c.marks,
                   "-" if self._l_rate is None else "%.0f" % self._l_rate,
                   self.handler.malformed_l if self.handler is not None else 0))
            return
        drops = "-" if c.device_snap_drops is None else str(c.device_snap_drops)
        self.card.set_counters("saved %d | rejected %d | device drops %s | EVT w/o snapshot %d\n"
                               "not armed %d | saved w/o EVT %d | save errors %d | snapshots %d"
                               % (c.saved, c.rejected, drops, c.evt_without_snap, c.not_armed,
                                  c.saved_without_evt, c.save_errors, c.snapshots))

    def _refresh_log_controls(self) -> None:
        """Start log, Record, the session box and the log status line."""
        log_reason = self._log_block_reason()
        rec_reason = self._record_block_reason()
        recording = self.recorder.recording
        self.log_button.setText("Stop log" if self._logging else "Start log")
        self.log_button.setEnabled(self._logging or not log_reason)
        self.log_button.setToolTip("" if self._logging else log_reason)
        set_flag(self.log_button, "armed", self._logging)
        self.record_button.setText("Recording… %.1f s" % self.recorder.remaining_s()
                                   if recording else "Record (%s)" % cfg.MANUAL_RECORD_KEY)
        self.record_button.setEnabled(not recording and not rec_reason)
        self.record_button.setToolTip(rec_reason)
        set_flag(self.record_button, "armed", recording)
        # The session stays fixed while a log or a recording uses it.
        self.session_edit.setEnabled(not self._logging and not recording)
        self.log_label.setVisible(bool(self._log_text))
        self.log_label.setText(self._log_text)

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
            self.handler.stop()  # _on_reader_finished resets the buttons
            return
        if self.handler is not None:
            self.handler.deleteLater()  # The previous, finished reader
            self.handler = None
        simulated = self.sim_check.isChecked()
        sim_build = self.sim_build_combo.currentData()
        port = None
        if not simulated:
            device = self.port_combo.currentData()
            port = next((p for p in self._ports if p.device == device), None)
            if port is None:
                self._on_error("Choose a port (or Simulated device) first")
                return
            self.settings.setValue("last_port", port.device)
        self.handler = SerialHandler(self)
        self.handler.snapshot_received.connect(self._on_snapshot)
        self.handler.snapshot_rejected.connect(self._on_rejected)
        self.handler.event_received.connect(self._on_event)
        self.handler.boot_received.connect(self._on_boot)
        self.handler.config_received.connect(self._on_config)
        self.handler.health_received.connect(self._on_health)
        self.handler.fault_received.connect(self._on_fault)
        self.handler.sample_received.connect(self._on_s_line)
        self.handler.samples_received.connect(self._on_samples)
        self.handler.log_status.connect(self._on_log_status)
        self.handler.connection_changed.connect(self._on_connection)
        self.handler.reconnected.connect(self._on_reconnected)
        self.handler.finished.connect(self._on_reader_finished)
        self.handler.configure(port, simulated, sim_build)
        self.live_plot.reset()
        self.card.clear_fault()          # A fault stays up until the next Connect
        self.card.set_simulated(False)
        self.ctrl.on_new_connection()    # The last connection's BOOT may not apply now
        self._boot_info = ""
        self._health_info = ""
        self._show_strip()
        self._build = _BUILD_UNKNOWN
        self._logging = False
        self._log_text = ""
        self._port_name = "simulated (%s build)" % sim_build if simulated else port.label()
        self._l_count = 0
        self._rate_mark = None
        self._l_rate = None
        self.recorder.interrupt("new connection")
        self.handler.start()
        _LOG.info("Connect: %s", self._port_name)
        self.connect_button.setText("Disconnect")
        self.sim_check.setEnabled(False)
        self.sim_build_combo.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.port_combo.setEnabled(False)
        self._refresh()

    def _on_sim_toggled(self, on: bool) -> None:
        """
        @param on  Simulated device checkbox: the port list or the sim build.
        """
        self.port_combo.setEnabled(not on)
        self.sim_build_combo.setEnabled(on)

    def _apply_session(self) -> None:
        """Takes the session name from the text box if it is valid."""
        # Leave the text box, so P / B / F label takes instead of typing here.
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
            self.settings.setValue("session", name)
            _LOG.info("Session: %s", name)
            self.session_changed.emit(name)
            self.card.clear_recent()

    def _apply_label(self, label: str) -> None:
        """
        @param label  The dropdown's new label.
        """
        self.ctrl.label = label
        self._refresh()

    def _toggle_arm(self) -> None:
        """Arm / Cancel arm button."""
        if self.ctrl.armed:
            self.ctrl.disarm()
        elif self.ctrl.session:
            self.ctrl.arm()
        else:
            self._on_error("Enter a valid session name before arming")
        self._refresh()

    def _apply_keep_armed(self, on: bool) -> None:
        """
        @param on  Keep armed checkbox state.
        """
        if on and not self.ctrl.session:
            self.keep_check.setChecked(False)
            self._on_error("Enter a valid session name before arming")
            return
        self.ctrl.keep_armed = on
        if on:
            self.ctrl.disarm()  # Keep armed replaces a single Arm
        _LOG.info("Keep armed %s (label %s)", "on" if on else "off", self.ctrl.label)
        self._refresh()

    def _quick_label(self, label: str) -> None:
        """
        P / B / F: labels the most recent take.
        @param label  The label for the key.
        """
        claim = self.ctrl.last_claim
        if claim is None:
            self._on_error("No take to label yet")
            return
        old = claim.path
        new = self.ctrl.quick_label(label)
        if new is not None:
            _LOG.info("Quick label: %s -> %s", old.name if old else "?", new.name)
            if old is not None and old != new:
                self.card.rename_recent(old.stem, new.stem)
                self.card.show_take(OK, new.stem, "Relabeled (was %s)" % old.stem)
            else:
                self.card.show_take(OK, new.stem, "Already labeled %s" % label)
            self.take_saved.emit(new)
        elif claim.path is None:
            _LOG.info("Quick label: pending take will be saved as %s", label)
            self.card.show_take(ARMED, label, "Pending: the latest take is waiting for its EVT "
                                "and will be saved as %s" % label)

    def _label_key(self, label: str) -> None:
        """
        P / B / F. Record build: labels the latest take (_quick_label). Log
        build (owner, 2026-11-22): writes a MARK if the drive log is running,
        and records a take with this label; during a recording's countdown it
        sets that recording's label instead of starting another.
        @param label  The label for the key.
        """
        if self._build != _BUILD_LOG:
            self._quick_label(label)
            return
        marked = self._write_mark(label)
        if self.recorder.recording:
            self.recorder.set_label(label)
            _LOG.info("Key %s: the recording will be saved as %s%s", label, label,
                      " (marked)" if marked else "")
            self.card.show_take(ARMED, label, "The recording will be saved as %s%s"
                                % (label, "; marked in the log" if marked else ""))
            self._refresh()
            return
        self._manual_record(label, marked=marked)

    def _write_mark(self, label: str) -> bool:
        """
        @param label  A label, or manual_<LABEL> for a Record press.
        @return True if a MARK line was sent to the running drive log.
        """
        if not self._logging or self.handler is None or not self.handler.isRunning():
            return False
        last_n = self.recorder.last_n
        self.handler.write_mark(int(time.time() * 1000), -1 if last_n is None else last_n, label)
        self.ctrl.count_mark()
        _LOG.info("MARK %s at n %s", label, last_n)
        return True

    def _manual_record(self, label: str, marked: bool = False) -> None:
        """
        Record button, R, or P / B / F in the log build: starts collecting
        MANUAL_POST_S more samples after the press.
        @param label   Label to save the take under.
        @param marked  A key's MARK was already written (for the card text).
        """
        reason = self._record_block_reason()
        if not reason:
            reason = self.recorder.press(label, int(time.time() * 1000),
                                         self.ctrl.boot.raw if self.ctrl.boot else None)
        if reason:
            if marked:
                self.card.show_take(OK, "MARKED", "%s marked in the log; no take: %s"
                                    % (label, reason))
            else:
                self._on_error(reason[0].upper() + reason[1:])
            return
        epoch_ms, press_n = self.recorder.press_info
        if self._logging and self.handler is not None:
            # Same time and n as the raw recording, so cut_windows.py can tell
            # which part of the log this recording already covers.
            self.handler.write_mark(epoch_ms, press_n, "manual_" + label)
            self.ctrl.count_mark()
        _LOG.info("Record: %s at n %d%s", label, press_n, " (marked)" if marked else "")
        self._refresh()

    def _finish_capture(self, cap) -> None:
        """
        A recording ended: saves the raw file and its take, and shows the
        outcome on the card (green with the take's name, amber with the reason).
        @param cap  The manual_record.Capture.
        """
        session = self.ctrl.session
        out = save_capture(self.store, session, cap, self.ctrl.gui_commit)
        self.ctrl.count_manual(out.state)
        raw = " · raw kept as %s/%s" % (cfg.MANUAL_DIR_NAME, out.raw_path.name) \
            if out.raw_path is not None else ""
        if out.take_path is not None:
            name = out.take_path.stem
            sub = "Manual take: %s · %s" % (out.message, self._take_summary(out.take_path))
            self.card.add_recent(name)
            self.card.show_take(OK, name, sub)
            al = out.result.alignment
            values = [s.model for s in cap.rows[al.index - cfg.TAKE_N_PRE:
                                                al.index + cfg.TAKE_N_POST]]
            rate = float(out.result.header.get("rate_hz", cfg.DEFAULT_RATE_HZ))
            self.last_plot.show_values(values, cfg.TAKE_N_PRE, rate, "manual  %s  %s  align %s"
                                       % (name, cap.label, al.kind))
            _LOG.info("Manual take %s/%s (%s)%s", session, name, out.message, raw)
            self.take_saved.emit(out.take_path)
        else:
            headline = "NO TAKE" if out.state == NO_TAKE else "NOT SAVED"
            self.card.show_take(WARN, headline, out.message + raw)
            _LOG.warning("Record: %s: %s%s", headline.lower(), out.message, raw)
        self._refresh()

    def _toggle_log(self) -> None:
        """Start log / Stop log button."""
        if self.handler is None or not self.handler.isRunning():
            self._logging = False
            self._refresh()
            return
        if self._logging:
            self.handler.stop_log()     # Its final status confirms it
            self._logging = False
            _LOG.info("Stop log")
            self._refresh()
            return
        reason = self._log_block_reason()
        if reason:
            self._on_error(reason)
            return
        drives = self.store.session_dir(self.ctrl.session) / cfg.DRIVES_DIR_NAME
        # Known: _log_block_reason() waits for the settings. The header keeps
        # them because the log usually starts after BOOT went by.
        boot = self.ctrl.boot.raw if self.ctrl.boot else ""
        self.handler.start_log(drives, {"session": self.ctrl.session,
                                        "gui_commit": self.ctrl.gui_commit,
                                        "port": self._port_name, "boot": boot})
        self._logging = True
        self._log_text = "Starting the log in %s/%s/ ..." % (self.ctrl.session,
                                                             cfg.DRIVES_DIR_NAME)
        _LOG.info("Start log in %s", drives)
        self._refresh()

    def take_moved(self, old: Path, new: Optional[Path]) -> None:
        """
        The collected list relabeled or deleted a take; keeps the "most recent
        take" pointer for quick labels correct.
        @param old  Old path.
        @param new  New path, or None if deleted.
        """
        claim = self.ctrl.last_claim
        if claim is not None and claim.path == old:
            if new is None:
                self.ctrl.last_claim = None
            else:
                claim.path = new
                parsed = parse_take_name(new)
                if parsed is not None:
                    claim.label = parsed[0]
        _LOG.info("Take %s -> %s", old.name, new.name if new else "deleted")
        self.card.rename_recent(old.stem, new.stem if new else None)

    def shutdown(self) -> None:
        """Stops the reader thread (window closing)."""
        self.timer.stop()
        if self.handler is not None and self.handler.isRunning():
            self.handler.stop()

    # --------------------------------------------------------- reader signals

    def _on_snapshot(self, snap, wall: float) -> None:
        """
        @param snap  A validated Snapshot.
        @param wall  Wall time it arrived.
        """
        if self._build == _BUILD_UNKNOWN:
            self._set_build(_BUILD_RECORD)   # Snapshots: the record build
        will_save = self.ctrl.armed or self.ctrl.keep_armed
        self.ctrl.on_snapshot(snap, wall, time.monotonic())
        self.last_plot.show_values(
            snap.values, snap.n_pre, snap.rate_hz,
            # Saved or not first: the line is cut off with "..." when too long.
            "%s  seq %d  trig_idx %d  %d rows  fw %s" % (
                "saving" if will_save else "not armed: not saved",
                snap.seq, snap.trig_idx, snap.n_rows, snap.fw))
        self.card.set_simulated(snap.fw.startswith("sim-"))
        self._refresh()

    def _on_event(self, evt) -> None:
        """
        @param evt  An EventLine.
        """
        if self._build != _BUILD_LOG:
            # Paired with its snapshot. The log build sends no snapshots, so
            # there every EVT would time out as "had no valid snapshot"; its
            # EVTs are in the drive log for cut_windows.py instead.
            self.ctrl.on_event(evt, time.monotonic())
        _LOG.info("EVT seq %d class %s", evt.seq, evt.fields.get("class", "?"))
        snap = self.ctrl.last_snapshot
        if snap is not None and snap.seq == evt.seq:
            self.last_plot.info.setText(self.last_plot.info.text() +
                                        "  rule %s" % evt.fields.get("class", "?"))
        self._refresh()

    def _on_rejected(self, rej) -> None:
        """
        @param rej  A Rejected snapshot.
        """
        self.ctrl.on_rejected(rej)
        seq = "" if rej.seq is None else " seq %d" % rej.seq
        self.card.show_take(WARN, "REJECTED", ("" if rej.seq is None else "seq %d: " % rej.seq)
                            + rej.reason)
        _LOG.warning("Snapshot rejected%s: %s", seq, rej.reason)
        self._refresh()

    def _on_boot(self, boot) -> None:
        """
        @param boot  A BootLine: the board (re)booted. Settles which build is
                     connected.
        """
        self.ctrl.on_boot(boot)
        cap = self.recorder.interrupt("the board rebooted during the recording")
        _LOG.info("BOOT: %s", boot.raw)
        self._apply_settings(boot, from_cfg=False)
        if cap is not None:
            self._finish_capture(cap)

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
        Shows the board's settings in the strip, sizes the manual Record
        buffer by its rate, and settles which build is connected.
        @param boot      A BOOT line, or a CFG's as_boot().
        @param from_cfg  The settings came from a CFG line (BOOT was missed).
        """
        s = {k: html.escape(v) for k, v in boot.settings.items()}
        rec = s.get("rec", "?")
        log = s.get("log", "0") == "1"
        if log:
            build_text = "log build (L lines, no snapshots)"
            take_text = "takes %d+%d rows at %s Hz, cut on the PC" % (
                cfg.TAKE_N_PRE, cfg.TAKE_N_POST, s.get("odr", "?"))
        else:
            build_text = "record build" if rec == "1" else \
                self._warn_html("rec=%s: NOT a record or log build, no snapshots" % rec)
            take_text = "snapshot %s+%s rows at %s Hz" % (
                s.get("snap_pre", "?"), s.get("snap_post", "?"),
                s.get("snap_rate", s.get("odr", "?")))
        self._boot_info = " | ".join((
            "fw %s%s" % (s.get("fw", "?"), " (settings from CFG)" if from_cfg else ""),
            build_text,
            "thr z %s g, roll %s, pitch %s dps" % (s.get("thr_z", "?"), s.get("thr_roll", "?"),
                                                  s.get("thr_pitch", "?")),
            "rearm %s, hold %s ms" % (s.get("rearm_frac", "?"), s.get("rearm_hold_ms", "?")),
            take_text))
        self._show_strip()
        self.card.set_simulated(boot.settings.get("fw", "").startswith("sim-"))
        try:
            self.recorder.set_rate(int(boot.settings.get("odr", cfg.DEFAULT_RATE_HZ)))
        except ValueError:
            pass   # Keeps the default rate
        self._set_build(_BUILD_LOG if log else _BUILD_RECORD if rec == "1" else _BUILD_PRODUCT)

    def _set_build(self, build: str) -> None:
        """
        @param build  The build now known to be connected. The log build turns
                      Arm and Keep armed off (no snapshots come).
        """
        if build != self._build:
            _LOG.info("Board build: %s", build)
        self._build = build
        if build == _BUILD_LOG and (self.ctrl.armed or self.ctrl.keep_armed):
            self.ctrl.disarm()
            self.keep_check.setChecked(False)   # Calls _apply_keep_armed
        self._refresh()

    def _on_s_line(self, s) -> None:
        """
        @param s  An S line: to the live view; S lines mean it is not the log build.
        """
        if self._build == _BUILD_UNKNOWN:
            self._set_build(_BUILD_RECORD)
        self.live_plot.add_sample(s)

    def _on_samples(self, batch: list) -> None:
        """
        A batch of L samples (log build): every LIVE_DECIMATE-th to the live
        view, all of them to the manual recorder.
        @param batch  LogSample list, oldest first.
        """
        if self._build == _BUILD_UNKNOWN:
            self._set_build(_BUILD_LOG)
        self._l_count += len(batch)
        for s in batch:
            if s.n % cfg.LIVE_DECIMATE == 0:
                # Shown as an S line would be: chip axes (gyro bias in), then z_hp.
                self.live_plot.add_sample(SampleLine(s.n, s.t_us, s.raw + s.model[:1]))
        was_recording = self.recorder.recording
        cap = self.recorder.feed(batch)
        if cap is not None:
            self._finish_capture(cap)
        elif was_recording:
            self._refresh()          # The countdown

    def _on_log_status(self, st) -> None:
        """
        @param st  A drive_log.LogStatus from the reader thread.
        """
        name = st.path.name if st.path is not None else "drive log"
        parts = ["%s %s" % ("LOGGING" if st.active else "Saved", html.escape(name)),
                 "%d:%02d" % divmod(int(st.elapsed_s), 60),
                 "%.1f MB" % (st.size / 1e6)]
        if st.active:
            parts.append("%s L/s" % ("-" if st.l_rate is None else "%.0f" % st.l_rate))
        missing = "missing samples %d, timing gaps %d" % (st.missing, st.timing_gaps)
        parts.append(self._warn_html(missing) if st.missing or st.timing_gaps
                     else html.escape(missing))
        if st.marks:
            parts.append("marks %d" % st.marks)
        if st.free_bytes is not None:
            free = "%.1f GB free" % (st.free_bytes / 1e9)
            parts.append(self._warn_html("LOW DISK: " + free) if st.low_disk else free)
        if st.error:
            parts.append(self._warn_html("stopped: " + st.error))
            self._on_error("Drive log %s stopped: %s" % (name, st.error))
        self._log_text = " · ".join(parts)
        if not st.active:
            self._logging = False
            if st.path is not None:
                _LOG.info("Drive log closed: %s (%.1f MB, missing %d, timing gaps %d)",
                          st.path, st.size / 1e6, st.missing, st.timing_gaps)
        self._refresh()

    def _on_health(self, hlt) -> None:
        """
        @param hlt  A HealthLine.
        """
        self.ctrl.on_health(hlt)
        drops = self.ctrl.counters.device_snap_drops
        if drops is not None and drops != self._last_drops:
            if self._last_drops is not None or drops:
                _LOG.warning("Device snap_drops now %d", drops)
            self._last_drops = drops
        rate = self.live_plot.lines_per_second()
        now = time.monotonic()
        if self._build == _BUILD_LOG:
            # L lines per second since the last HLT (the live view is decimated).
            if self._rate_mark is not None and now - self._rate_mark[0] > 0.2:
                self._l_rate = (self._l_count - self._rate_mark[1]) / (now - self._rate_mark[0])
            rate = self._l_rate
        self._rate_mark = (now, self._l_count)
        counts = [(name, hlt.fields[key]) for name, key in
                  (("sample q", "sample_q_drops"), ("out q", "out_q_drops"),
                   ("serial", "serial_drops"), ("snap", "snap_drops")) if key in hlt.fields]
        drop_text = "drops: " + ", ".join("%s %d" % nc for nc in counts)
        if any(n for _name, n in counts):
            drop_text = self._warn_html(drop_text)
        self._health_info = "%s lines/s | %s" % ("-" if rate is None else "%.1f" % rate,
                                                 drop_text)
        self._show_strip()
        self._refresh()

    def _on_fault(self, flt) -> None:
        """
        @param flt  A FaultLine.
        """
        self.card.show_fault("FAULT %s" % flt.code, "DEVICE FAULT: %s %s \u00b7 stays until the "
                             "next Connect" % (flt.code, flt.name))
        _LOG.warning("FLT %s %s", flt.code, flt.name)

    def _on_connection(self, ok: bool, message: str) -> None:
        """
        @param ok       Connected? (The message says it; kept for the log.)
        @param message  What happened.
        """
        self._connected = ok
        self._set_conn(OK if ok else WARN, message)
        self.live_plot.set_connected(ok)
        _LOG.info("Connection (%s): %s", "up" if ok else "down", message)
        cap = None if ok else self.recorder.interrupt("USB lost during the recording")
        self._refresh()
        if cap is not None:
            self._finish_capture(cap)

    def _on_reader_finished(self) -> None:
        """The reader thread ended (Disconnect, or an error): back to Connect.
        The reader closed the drive log on its way out."""
        self.live_plot.set_connected(False)
        self.connect_button.setText("Connect")
        self.sim_check.setEnabled(True)
        self.sim_build_combo.setEnabled(self.sim_check.isChecked())
        self.refresh_button.setEnabled(True)
        self.port_combo.setEnabled(not self.sim_check.isChecked())
        self._connected = False
        self._logging = False
        set_state(self.conn_dot, IDLE)
        cap = self.recorder.interrupt("disconnected during the recording")
        self._refresh()
        if cap is not None:
            self._finish_capture(cap)

    def _on_reconnected(self) -> None:
        """The port came back: nothing waiting can be paired any more, and
        the build is judged again (a different firmware may be flashed)."""
        self.ctrl.on_reconnect()
        cap = self.recorder.interrupt("USB reconnected during the recording")
        self._boot_info = ("Device settings: waiting for BOOT or CFG (cleared on reconnect; "
                           "CFG comes every %.0f s)" % cfg.CFG_PERIOD_S)
        self._show_strip()
        self._build = _BUILD_UNKNOWN
        _LOG.info("Reconnected: unmatched items cleared")
        self._refresh()
        if cap is not None:
            self._finish_capture(cap)

    # ------------------------------------------------------ controller hooks

    def _on_saved(self, path: Path, claim: Claim) -> None:
        """
        @param path   The take just written.
        @param claim  Its label and session.
        """
        self.card.add_recent(path.stem)
        self.card.show_take(OK, path.stem, self._take_summary(path))
        _LOG.info("Saved %s/%s", claim.session, path.name)
        self.take_saved.emit(path)

    def _on_error(self, message: str) -> None:
        """
        @param message  Shown on the status line and logged; never a popup.
        """
        self.msg_label.setText(message)
        self.msg_label.show()
        _LOG.error(message)

    def _on_info(self, message: str) -> None:
        """
        @param message  Logged (EVT without snapshot, take without EVT).
        """
        _LOG.info(message)

    def _tick(self) -> None:
        """Every 250 ms: EVT pairing timeouts."""
        before = self.ctrl.counters.saved + self.ctrl.counters.evt_without_snap
        self.ctrl.tick(time.monotonic())
        if self.ctrl.counters.saved + self.ctrl.counters.evt_without_snap != before:
            self._refresh()
