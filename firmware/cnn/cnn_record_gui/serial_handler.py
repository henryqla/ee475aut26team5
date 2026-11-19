"""
File:        serial_handler.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Adapted from: Python/arduino_serial_handler.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             QThread that reads lines and emits Qt signals. Changed: any port
             can be chosen (the framework skipped Espressif VID 0x303A and
             only accepted XIAO boards); the baud comes from cnn_config;
             lines go through SnapshotAssembler instead of IMU: / PROB:
             parsing (S lines feed the live IMU view); it reconnects by itself after the port disappears; it
             never sends anything to the device; the codegen_task import is
             gone; and it can read from the simulated device instead.
Description: Background reader for the record GUI. Owns the serial port (or
             the simulated device), splits the byte stream into lines,
             assembles and validates snapshots, and emits one Qt signal per
             complete item. All decisions about saving happen in the GUI
             thread (record_core). A reset re-enumerates the S3's native USB,
             so when the port disappears the reader keeps retrying the same
             device (by name, or by VID / PID / serial number if Windows gives
             it a new name) and emits reconnected when it is back.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QThread, pyqtSignal

import cnn_config as cfg
from sim_source import SimDevice, SimDisconnect
from snapshot_protocol import (BootLine, EventLine, FaultLine, HealthLine, Rejected,
                               SampleLine, Snapshot, SnapshotAssembler)

# ============================================================================
# Constants
# ============================================================================

# Bytes kept while waiting for a line ending; more than this without one is
# garbage (e.g. the wrong baud on a UART port) and is discarded.
_MAX_PARTIAL_LINE = 4096

# ============================================================================
# Classes
# ============================================================================


@dataclass
class PortInfo:
    """One serial port as the OS lists it."""
    device: str                       # e.g. "COM5"
    description: str
    vid: Optional[int]
    pid: Optional[int]
    serial_number: Optional[str]

    def label(self) -> str:
        """
        @return Text for the port dropdown, with VID:PID when known.
        """
        ids = " [%04X:%04X]" % (self.vid, self.pid) if self.vid is not None else ""
        return "%s  %s%s" % (self.device, self.description, ids)


def list_ports() -> list:
    """
    @return Every serial port the OS lists, as PortInfo, sorted by name. No
            filtering: the S3 DevKit's native USB is VID 0x303A, PID 0x1001.
    """
    ports = []
    for p in serial.tools.list_ports.comports():
        ports.append(PortInfo(p.device, p.description or "", p.vid, p.pid, p.serial_number))
    return sorted(ports, key=lambda p: p.device)


class SerialHandler(QThread):
    """Reads one port (or the simulated device) until stop() is called."""

    snapshot_received = pyqtSignal(object, float)   # Snapshot, wall time it arrived
    snapshot_rejected = pyqtSignal(object)          # Rejected
    event_received = pyqtSignal(object)             # EventLine
    boot_received = pyqtSignal(object)              # BootLine
    health_received = pyqtSignal(object)            # HealthLine
    fault_received = pyqtSignal(object)             # FaultLine
    sample_received = pyqtSignal(object)            # SampleLine (live view only)
    connection_changed = pyqtSignal(bool, str)      # Connected?, message
    reconnected = pyqtSignal()                      # Back after a loss: seq may restart

    def __init__(self, parent=None) -> None:
        """
        @param parent  Qt parent, or None.
        """
        super().__init__(parent)
        self._running = False
        self._port: Optional[PortInfo] = None
        self._simulated = False
        self._asm = SnapshotAssembler(cfg.SNAP_STALL_TIMEOUT_S, cfg.SNAP_MAX_ROWS)

    def configure(self, port: Optional[PortInfo], simulated: bool) -> None:
        """
        Chooses the source. Call before start(), while the thread is stopped.
        @param port       The port to read (ignored if simulated).
        @param simulated  Read the simulated device instead of a port.
        """
        self._port = port
        self._simulated = simulated

    def stop(self) -> None:
        """Asks the thread to finish and waits for it (at most ~3 s)."""
        self._running = False
        self.wait(3000)

    def run(self) -> None:
        """Thread body. Never lets an exception end the thread silently."""
        self._running = True
        self._asm = SnapshotAssembler(cfg.SNAP_STALL_TIMEOUT_S, cfg.SNAP_MAX_ROWS)
        try:
            if self._simulated:
                self._run_sim()
            elif self._port is not None:
                self._run_port()
        except Exception as exc:  # Report, so the GUI shows why reading stopped
            self.connection_changed.emit(False, "Reader stopped: %r" % exc)
        self._emit(self._asm.abort("disconnected mid-snapshot"))
        self.connection_changed.emit(False, "Disconnected")

    # ---------------------------------------------------------------- helpers

    def _sleep(self, seconds: float) -> None:
        """
        Sleeps in short steps so stop() takes effect quickly.
        @param seconds  How long.
        """
        end = time.monotonic() + seconds
        while self._running:
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def _emit(self, items: list) -> None:
        """
        Emits one signal per parsed item.
        @param items  Output of the assembler.
        """
        for x in items:
            if isinstance(x, Snapshot):
                self.snapshot_received.emit(x, time.time())
            elif isinstance(x, Rejected):
                self.snapshot_rejected.emit(x)
            elif isinstance(x, EventLine):
                self.event_received.emit(x)
            elif isinstance(x, BootLine):
                self.boot_received.emit(x)
            elif isinstance(x, HealthLine):
                self.health_received.emit(x)
            elif isinstance(x, FaultLine):
                self.fault_received.emit(x)
            elif isinstance(x, SampleLine):
                self.sample_received.emit(x)

    def _open(self) -> Optional[serial.Serial]:
        """
        Opens the chosen port, or the same device under a new name.
        DTR and RTS are set low before opening, so opening the port never
        drives the S3's auto-reset lines.
        @return The open port, or None if it is not there (yet).
        """
        names = [self._port.device]
        if self._port.vid is not None and self._port.serial_number:
            for p in list_ports():
                if (p.vid, p.pid, p.serial_number) == \
                        (self._port.vid, self._port.pid, self._port.serial_number) \
                        and p.device not in names:
                    names.append(p.device)
        for name in names:
            ser = serial.Serial()
            ser.port = name
            ser.baudrate = cfg.GUI_BAUD
            ser.timeout = cfg.SERIAL_READ_TIMEOUT_S
            ser.dtr = False
            ser.rts = False
            try:
                ser.open()
                return ser
            except (serial.SerialException, OSError, ValueError):
                continue
        return None

    def _run_port(self) -> None:
        """Read loop for a real port, with automatic reconnection."""
        first = True
        waiting_reported = False
        while self._running:
            ser = self._open()
            if ser is None:
                if not waiting_reported:  # Once per outage, not every retry
                    self.connection_changed.emit(False, "Waiting for %s ..." % self._port.device)
                    waiting_reported = True
                self._sleep(cfg.RECONNECT_PERIOD_S)
                continue
            waiting_reported = False
            self.connection_changed.emit(True, "Connected to %s" % ser.port)
            if not first:
                self.reconnected.emit()
            first = False
            buf = bytearray()
            try:
                while self._running:
                    # Up to the read timeout for the first byte, then whatever
                    # else is already waiting.
                    data = ser.read(ser.in_waiting or 1)
                    now = time.monotonic()
                    if data:
                        buf.extend(data)
                        while True:
                            k = buf.find(b"\n")
                            if k < 0:
                                break
                            line = bytes(buf[:k]).decode("utf-8", errors="replace")
                            del buf[:k + 1]
                            self._emit(self._asm.feed(line, now))
                        if len(buf) > _MAX_PARTIAL_LINE:
                            buf.clear()
                    self._emit(self._asm.check_stall(now))
            except (serial.SerialException, OSError) as exc:
                # Unplugged or reset: the partial snapshot is discarded.
                self._emit(self._asm.abort("USB lost mid-snapshot"))
                self.connection_changed.emit(False, "Lost %s (%s); retrying" % (ser.port, exc))
            finally:
                try:
                    ser.close()
                except (serial.SerialException, OSError):
                    pass

    def _run_sim(self) -> None:
        """
        Read loop for the simulated device, paced in real time against a
        schedule: each line is due a fixed time after the one before, so a
        coarse OS sleep (~15.6 ms steps on Windows) delays a line a little but
        never slows the device down (S lines are only ~19 ms apart).
        """
        self.connection_changed.emit(True, "Simulated device (fw %s)" % cfg.SIM_FW)
        due = time.monotonic()
        for delay, item in SimDevice().stream():
            if not self._running:
                return
            due += delay
            wait = due - time.monotonic()
            if wait > 0:
                self._sleep(wait)
                if not self._running:
                    return
            elif wait < -1.0:
                due = time.monotonic()  # Far behind (PC busy): carry on, don't burst
            now = time.monotonic()
            if isinstance(item, SimDisconnect):
                self._emit(self._asm.abort("USB lost mid-snapshot (simulated)"))
                self.connection_changed.emit(False, "Simulated USB drop-out")
                self._sleep(item.duration_s)
                if not self._running:
                    return
                self.connection_changed.emit(True, "Simulated device (reconnected)")
                self.reconnected.emit()
                due = time.monotonic()
                continue
            self._emit(self._asm.feed(item, now))
            self._emit(self._asm.check_stall(now))
