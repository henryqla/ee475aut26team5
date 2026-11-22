"""
File:        record_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.2.0
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
from record_core import Claim, RecordController
from serial_handler import SerialHandler, list_ports
from snapshot_plot import SnapshotPlot
from status_card import ARMED, IDLE, OK, WARN, StatusCard
from take_store import TakeStore, load_take, parse_take_name

# ============================================================================
# Constants
# ============================================================================

_LOG = logging.getLogger("record")

# QSettings keys (per-user, remembered between runs).
_SETTINGS_ORG = "EE475Team5"
_SETTINGS_APP = "cnn_record_gui"

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

        # Row 1: simulated device, and Connect across the rest of the row
        connect_row = QHBoxLayout()
        self.sim_check = QCheckBox("Simulated device (no hardware)")
        self.sim_check.toggled.connect(lambda on: self.port_combo.setEnabled(not on))
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
        # Request 5 adds its Log and Record buttons here.

        layout.addWidget(self.msg_label)
        layout.addWidget(self._note("Firmware: firmware-bench built with RECORD_MODE 1."))

        # Quick labels: active while this panel's window is active and the
        # panel is visible. A focused text box keeps its own typing.
        for key, label in cfg.QUICK_LABEL_KEYS.items():
            sc = QShortcut(QKeySequence(key), self)
            sc.activated.connect(lambda lab=label: self._quick_label(lab))

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
        when = header.get("pc_received", "")
        try:
            when = datetime.datetime.fromisoformat(when).strftime("%H:%M:%S")
        except ValueError:
            pass  # Shown as written
        return " \u00b7 ".join(("seq %s" % header.get("seq", "?"),
                                 "rule %s" % header.get("rule_class", "?"),
                                 "%s rows" % header.get("n_rows", "?"), when))

    def _refresh(self) -> None:
        """Redraws the card's base state, the counters and the Arm button."""
        c = self.ctrl.counters
        label = self.ctrl.label
        running = self.handler is not None and self.handler.isRunning()
        if not self._connected:
            self.card.set_base(IDLE, "NOT CONNECTED", self._conn_message if running else
                               "Pick a port or Simulated device, then Connect")
        elif self.ctrl.keep_armed:
            self.card.set_base(ARMED, "KEEP ARMED",
                               "Waiting for a snapshot \u00b7 label %s" % label)
        elif self.ctrl.armed:
            self.card.set_base(ARMED, "ARMED", "Waiting for a snapshot \u00b7 label %s" % label)
        else:
            self.card.set_base(IDLE, "READY", "Snapshots are shown, not saved \u00b7 press Arm "
                               "to save the next one", keeps_take=True)
        self.arm_button.setText("Cancel arm" if self.ctrl.armed else "Arm")
        self.arm_button.setEnabled(not self.ctrl.keep_armed)
        set_flag(self.arm_button, "armed", self.ctrl.armed or self.ctrl.keep_armed)
        drops = "-" if c.device_snap_drops is None else str(c.device_snap_drops)
        self.card.set_counters("saved %d | rejected %d | device drops %s | EVT w/o snapshot %d\n"
                               "not armed %d | saved w/o EVT %d | save errors %d | snapshots %d"
                               % (c.saved, c.rejected, drops, c.evt_without_snap, c.not_armed,
                                  c.saved_without_evt, c.save_errors, c.snapshots))

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
        self.handler.health_received.connect(self._on_health)
        self.handler.fault_received.connect(self._on_fault)
        self.handler.sample_received.connect(self.live_plot.add_sample)
        self.handler.connection_changed.connect(self._on_connection)
        self.handler.reconnected.connect(self._on_reconnected)
        self.handler.finished.connect(self._on_reader_finished)
        self.handler.configure(port, simulated)
        self.live_plot.reset()
        self.card.clear_fault()          # A fault stays up until the next Connect
        self.card.set_simulated(False)
        self._boot_info = ""
        self._health_info = ""
        self._show_strip()
        self.handler.start()
        _LOG.info("Connect: %s", "simulated device" if simulated else port.label())
        self.connect_button.setText("Disconnect")
        self.sim_check.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.port_combo.setEnabled(False)

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
        @param boot  A BootLine.
        """
        self.ctrl.on_boot(boot)
        s = {k: html.escape(v) for k, v in boot.settings.items()}
        rec = s.get("rec", "?")
        self._boot_info = " | ".join((
            "fw %s" % s.get("fw", "?"),
            "record build" if rec == "1" else
            self._warn_html("rec=%s: NOT a record build, no snapshots" % rec),
            "thr z %s g, roll %s, pitch %s dps" % (s.get("thr_z", "?"), s.get("thr_roll", "?"),
                                                  s.get("thr_pitch", "?")),
            "rearm %s, hold %s ms" % (s.get("rearm_frac", "?"), s.get("rearm_hold_ms", "?")),
            "snapshot %s+%s rows at %s Hz" % (s.get("snap_pre", "?"), s.get("snap_post", "?"),
                                              s.get("snap_rate", s.get("odr", "?")))))
        self._show_strip()
        _LOG.info("BOOT: %s", boot.raw)
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
        self._refresh()

    def _on_reader_finished(self) -> None:
        """The reader thread ended (Disconnect, or an error): back to Connect."""
        self.live_plot.set_connected(False)
        self.connect_button.setText("Connect")
        self.sim_check.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.port_combo.setEnabled(not self.sim_check.isChecked())
        self._connected = False
        set_state(self.conn_dot, IDLE)
        self._refresh()

    def _on_reconnected(self) -> None:
        """The port came back: nothing waiting can be paired any more."""
        self.ctrl.on_reconnect()
        self._boot_info = "Device settings: waiting for BOOT (lost on reconnect)"
        self._show_strip()
        _LOG.info("Reconnected: unmatched items cleared")
        self._refresh()

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
