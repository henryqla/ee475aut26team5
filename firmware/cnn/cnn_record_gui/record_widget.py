"""
File:        record_widget.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
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
Description: The "Record" panel. Owns the SerialHandler (reader thread) and
             the RecordController (record_core), connects one to the other,
             and shows connection, device settings, arm state and counters.
             Also passes the reader's S lines to the live IMU view (display
             only; they never reach the RecordController).
"""

# ============================================================================
# Imports
# ============================================================================

import datetime
import logging
import time
from pathlib import Path
from typing import Optional

from PyQt5.QtCore import QSettings, QTimer, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QGroupBox, QHBoxLayout,
                             QLabel, QLineEdit, QPushButton, QShortcut, QVBoxLayout)

import cnn_config as cfg
from app_styles import AppStyles
from live_plot import LivePlot
from record_core import Claim, RecordController
from serial_handler import SerialHandler, list_ports
from snapshot_plot import SnapshotPlot
from take_store import TakeStore, parse_take_name

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
                 live_plot: LivePlot) -> None:
        """
        @param store       Where takes are written.
        @param gui_commit  Commit of this checkout (into every take).
        @param last_plot   Where each arriving snapshot is drawn.
        @param live_plot   Where the S lines (live IMU samples) are drawn.
        """
        super().__init__("Record")
        self.setStyleSheet(AppStyles.get_group_box_style())
        self.store = store
        self.last_plot = last_plot
        self.live_plot = live_plot
        self.settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        self.ctrl = RecordController(store, gui_commit, on_saved=self._on_saved,
                                     on_error=self._on_error, on_info=self._on_info)
        self.handler: Optional[SerialHandler] = None
        self._ports = []
        self._last_drops = None  # Last device snap_drops logged

        layout = QVBoxLayout(self)
        grid = QGridLayout()
        layout.addLayout(grid)

        # Row 0: port
        grid.addWidget(self._label("Port"), 0, 0)
        self.port_combo = QComboBox()
        self.port_combo.setStyleSheet(AppStyles.get_combo_box_style())
        grid.addWidget(self.port_combo, 0, 1)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.setStyleSheet(AppStyles.get_button_style())
        self.refresh_button.clicked.connect(self._refresh_ports)
        grid.addWidget(self.refresh_button, 0, 2)

        # Row 1: simulated + connect
        self.sim_check = QCheckBox("Simulated device (no hardware)")
        self.sim_check.setStyleSheet(AppStyles.get_label_style())
        self.sim_check.toggled.connect(lambda on: self.port_combo.setEnabled(not on))
        grid.addWidget(self.sim_check, 1, 1)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setStyleSheet(AppStyles.get_button_style())
        self.connect_button.clicked.connect(self._toggle_connection)
        grid.addWidget(self.connect_button, 1, 2)

        # Row 2: session
        grid.addWidget(self._label("Session"), 2, 0)
        self.session_edit = QLineEdit(self.settings.value(
            "session", datetime.date.today().isoformat() + "_bench"))
        self.session_edit.setStyleSheet(AppStyles.get_line_edit_style())
        self.session_edit.editingFinished.connect(self._apply_session)
        grid.addWidget(self.session_edit, 2, 1, 1, 2)

        # Row 3: label
        grid.addWidget(self._label("Label"), 3, 0)
        self.label_combo = QComboBox()
        self.label_combo.setStyleSheet(AppStyles.get_combo_box_style())
        self.label_combo.addItems(cfg.LABELS)
        self.label_combo.setCurrentText(cfg.UNLABELED)
        self.label_combo.currentTextChanged.connect(self._apply_label)
        grid.addWidget(self.label_combo, 3, 1, 1, 2)

        # Row 4: arm
        arm_row = QHBoxLayout()
        self.arm_button = QPushButton("Arm")
        self.arm_button.setStyleSheet(AppStyles.get_button_style())
        self.arm_button.clicked.connect(self._toggle_arm)
        self.keep_check = QCheckBox("Keep armed")
        self.keep_check.setStyleSheet(AppStyles.get_label_style())
        self.keep_check.toggled.connect(self._apply_keep_armed)
        arm_row.addWidget(self.arm_button, 1)
        arm_row.addWidget(self.keep_check)
        layout.addLayout(arm_row)

        for text in ("Arm saves the next valid snapshot from the device under the label, "
                     "then disarms. Keep armed saves every one.",
                     "Car sessions: Keep armed with label UNLABELED; press %s to label the "
                     "latest take." % ", ".join("%s = %s" % kv for kv in
                                                cfg.QUICK_LABEL_KEYS.items()),
                     "Firmware: firmware-bench built with RECORD_MODE 1."):
            note = QLabel(text)
            note.setWordWrap(True)
            note.setStyleSheet(AppStyles.get_note_label_style())
            layout.addWidget(note)

        # Status lines
        self.status = {}
        for key in ("conn", "device", "arm", "counters", "last", "reject", "fault", "msg"):
            lab = QLabel("")
            lab.setWordWrap(True)
            lab.setStyleSheet(AppStyles.get_status_label_style())
            layout.addWidget(lab)
            self.status[key] = lab

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
        self._set_status("conn", "Not connected")
        self._refresh()

    # ------------------------------------------------------------ UI helpers

    @staticmethod
    def _label(text: str) -> QLabel:
        """
        @param text  Label text.
        @return A styled QLabel.
        """
        lab = QLabel(text)
        lab.setStyleSheet(AppStyles.get_label_style())
        return lab

    def _set_status(self, key: str, text: str) -> None:
        """
        @param key   Status line name.
        @param text  New text.
        """
        self.status[key].setText(text)

    def _refresh(self) -> None:
        """Redraws arm state and counters from the controller."""
        c = self.ctrl.counters
        if self.ctrl.keep_armed:
            arm = "KEEP ARMED: every snapshot is saved as %s" % self.ctrl.label
        elif self.ctrl.armed:
            arm = "ARMED: next snapshot is saved as %s" % self.ctrl.label
        else:
            arm = "Disarmed: snapshots are shown, not saved"
        self._set_status("arm", arm)
        self.arm_button.setText("Cancel arm" if self.ctrl.armed else "Arm")
        self.arm_button.setEnabled(not self.ctrl.keep_armed)
        drops = "-" if c.device_snap_drops is None else str(c.device_snap_drops)
        self._set_status("counters",
                         "saved %d | rejected %d | device drops %s | EVT w/o snapshot %d\n"
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
            self._set_status("last", "Latest take is now %s" % new.name)
            self.take_saved.emit(new)
        elif claim.path is None:
            _LOG.info("Quick label: pending take will be saved as %s", label)
            self._set_status("last", "Latest take (waiting for its EVT) will be saved as %s"
                             % label)

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
            "seq %d  trig_idx %d  %d rows  fw %s  %s" % (
                snap.seq, snap.trig_idx, snap.n_rows, snap.fw,
                "saving" if will_save else "not armed: not saved"))
        if snap.fw.startswith("sim-"):
            self._set_status("last", "Snapshot seq %d from the SIMULATED device" % snap.seq)
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
        self._set_status("reject", "Last rejected%s: %s" % (seq, rej.reason))
        _LOG.warning("Snapshot rejected%s: %s", seq, rej.reason)
        self._refresh()

    def _on_boot(self, boot) -> None:
        """
        @param boot  A BootLine.
        """
        self.ctrl.on_boot(boot)
        s = boot.settings
        rec = s.get("rec", "?")
        self._set_status("device", "fw %s | rec=%s | thr z %s g, roll %s, pitch %s dps | "
                         "rearm %s, hold %s ms | snapshot %s+%s rows%s" % (
                             s.get("fw", "?"), rec, s.get("thr_z", "?"), s.get("thr_roll", "?"),
                             s.get("thr_pitch", "?"), s.get("rearm_frac", "?"),
                             s.get("rearm_hold_ms", "?"), s.get("snap_pre", "?"),
                             s.get("snap_post", "?"),
                             "" if rec == "1" else "  (NOT a record build: no snapshots)"))
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
        self._set_status("fault", "")
        self._refresh()

    def _on_fault(self, flt) -> None:
        """
        @param flt  A FaultLine.
        """
        self._set_status("fault", "DEVICE FAULT: %s %s" % (flt.code, flt.name))
        _LOG.warning("FLT %s %s", flt.code, flt.name)

    def _on_connection(self, ok: bool, message: str) -> None:
        """
        @param ok       Connected? (The message says it; kept for the log.)
        @param message  What happened.
        """
        self._set_status("conn", message)
        self.live_plot.set_connected(ok)
        _LOG.info("Connection (%s): %s", "up" if ok else "down", message)

    def _on_reader_finished(self) -> None:
        """The reader thread ended (Disconnect, or an error): back to Connect."""
        self.live_plot.set_connected(False)
        self.connect_button.setText("Connect")
        self.sim_check.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.port_combo.setEnabled(not self.sim_check.isChecked())

    def _on_reconnected(self) -> None:
        """The port came back: nothing waiting can be paired any more."""
        self.ctrl.on_reconnect()
        self._set_status("device", "Device settings: waiting for BOOT (lost on reconnect)")
        _LOG.info("Reconnected: unmatched items cleared")
        self._refresh()

    # ------------------------------------------------------ controller hooks

    def _on_saved(self, path: Path, claim: Claim) -> None:
        """
        @param path   The take just written.
        @param claim  Its label and session.
        """
        self._set_status("last", "Saved %s/%s" % (claim.session, path.name))
        _LOG.info("Saved %s/%s", claim.session, path.name)
        self.take_saved.emit(path)

    def _on_error(self, message: str) -> None:
        """
        @param message  Shown on the status line and logged; never a popup.
        """
        self._set_status("msg", message)
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
