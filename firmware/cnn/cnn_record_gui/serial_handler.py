"""
File:        serial_handler.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.4.0
Change:      0.4.0 (training-live-inference-handoff.md, request G): the live
             inference lines. PROB lines are batched with the L samples and
             sent as prob_received (a list) right after each sample batch;
             DET and SELFTEST go out at once (det_received,
             selftest_received). malformed gives the per-tag counts. Two more
             simulated-device test controls: PROB lines on/off and malformed
             lines. The handler is shared by the Record and Live Inference
             tabs (RecordWidget.handler_started); it still never sends
             anything to the device.
             0.3.0 (manual-recording-overhaul-handoff.md, request B): only L,
             BOOT, CFG, HLT and FLT lines come in. Removed: the drive log, PC
             and MARK lines, the snapshot, EVT and S signals, and the
             simulated device's record / log builds (git tag
             pre-manual-overhaul). New: sim_command() passes the simulated
             device's test controls (inject an event, a gap, a reset, ok=0
             rows, a disconnect) from the GUI thread to this thread.
             0.2.1: CFG lines. 0.2.0: L samples batched every SAMPLE_BATCH_S.
Adapted from: Python/arduino_serial_handler.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             QThread that reads lines and emits Qt signals. Changed: any port
             can be chosen (the framework skipped Espressif VID 0x303A and
             only accepted XIAO boards); the baud comes from cnn_config; lines
             go through LineParser instead of IMU: / PROB: parsing; it
             reconnects by itself after the port disappears; it never sends
             anything to the device; the codegen_task import is gone; and it
             can read from the simulated device instead.
Description: Background reader for the record GUI. Owns the serial port (or
             the simulated device), splits the byte stream into lines, parses
             them, and hands L samples to the GUI thread in batches every
             SAMPLE_BATCH_S (one Qt signal per batch, not per sample; a batch
             goes out before any other item, so the order is kept). All
             decisions about clips happen in the GUI thread. A reset
             re-enumerates the S3's native USB, so when the port disappears
             the reader keeps retrying the same device (by name, or by VID /
             PID / serial number if Windows gives it a new name) and emits
             reconnected when it is back.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import queue
import time
from dataclasses import dataclass
from typing import Optional

import serial
import serial.tools.list_ports
from PyQt5.QtCore import QThread, pyqtSignal

import cnn_config as cfg
from sim_source import SimDevice, SimDisconnect
from snapshot_protocol import (BootLine, ConfigLine, DetLine, FaultLine, HealthLine, LineParser,
                               LogSample, ProbLine, SelftestLine)

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

    boot_received = pyqtSignal(object)              # BootLine
    config_received = pyqtSignal(object)            # ConfigLine (BOOT's settings, repeated)
    health_received = pyqtSignal(object)            # HealthLine
    fault_received = pyqtSignal(object)             # FaultLine
    samples_received = pyqtSignal(object)           # list of LogSample, batched
    prob_received = pyqtSignal(object)              # list of ProbLine, after each sample batch
    det_received = pyqtSignal(object)               # DetLine
    selftest_received = pyqtSignal(object)          # SelftestLine
    connection_changed = pyqtSignal(bool, str)      # Connected?, message
    reconnected = pyqtSignal()                      # Back after a loss: n may restart

    def __init__(self, parent=None) -> None:
        """
        @param parent  Qt parent, or None.
        """
        super().__init__(parent)
        self._running = False
        self._port: Optional[PortInfo] = None
        self._simulated = False
        self._parser = LineParser()
        self._sim: Optional[SimDevice] = None
        self._sim_cmds: queue.Queue = queue.Queue()   # Test controls from the GUI thread
        self._batch = []                 # LogSamples not yet sent to the GUI
        self._prob_batch = []            # ProbLines not yet sent to the GUI
        self._batch_t = 0.0              # Monotonic time the last batch was sent

    def configure(self, port: Optional[PortInfo], simulated: bool) -> None:
        """
        Chooses the source. Call before start(), while the thread is stopped.
        @param port       The port to read (ignored if simulated).
        @param simulated  Read the simulated device instead of a port.
        """
        self._port = port
        self._simulated = simulated

    @property
    def simulated(self) -> bool:
        """@return True if this reader runs the simulated device."""
        return self._simulated

    @property
    def malformed_l(self) -> int:
        """@return L lines ignored because they did not parse, this connection."""
        return self._parser.malformed_l

    @property
    def malformed(self) -> dict:
        """@return Lines ignored because they did not parse, per tag, this connection."""
        return dict(self._parser.malformed)

    def stop(self) -> None:
        """Asks the thread to finish and waits for it (at most ~3 s)."""
        self._running = False
        self.wait(3000)

    def sim_command(self, *cmd) -> None:
        """
        A simulated-device test control (GUI thread). Ignored for a real port.
        @param cmd  ("event", shape, delay_s), ("gap",), ("ok0",), ("reset",),
                    ("disconnect",), ("prob", on) or ("malformed",).
        """
        if self._simulated:
            self._sim_cmds.put(tuple(cmd))

    def run(self) -> None:
        """Thread body. Never lets an exception end the thread silently."""
        self._running = True
        self._parser = LineParser()
        try:
            if self._simulated:
                self._run_sim()
            elif self._port is not None:
                self._run_port()
        except Exception as exc:  # Report, so the GUI shows why reading stopped
            self.connection_changed.emit(False, "Reader stopped: %r" % exc)
        self._flush_batch()
        self.connection_changed.emit(False, "Disconnected")

    # ---------------------------------------------------------------- helpers

    def _sleep(self, seconds: float) -> None:
        """
        Sleeps in short steps so stop() takes effect quickly; keeps sending
        sample batches meanwhile.
        @param seconds  How long.
        """
        end = time.monotonic() + seconds
        while self._running:
            self._service(time.monotonic())
            left = end - time.monotonic()
            if left <= 0:
                return
            time.sleep(min(0.05, left))

    def _flush_batch(self) -> None:
        """Sends the waiting L samples, then the waiting PROB lines, to the GUI
        thread, if any."""
        if self._batch:
            self.samples_received.emit(self._batch)
            self._batch = []
        if self._prob_batch:
            self.prob_received.emit(self._prob_batch)
            self._prob_batch = []
        self._batch_t = time.monotonic()

    def _service(self, now: float) -> None:
        """
        Called often: sends the sample batch when it is due, and hands queued
        test controls to the simulated device.
        @param now  Monotonic time.
        """
        if (self._batch or self._prob_batch) and now - self._batch_t >= cfg.SAMPLE_BATCH_S:
            self._flush_batch()
        if self._sim is not None:
            while True:
                try:
                    cmd = self._sim_cmds.get_nowait()
                except queue.Empty:
                    break
                if cmd[0] == "event":
                    self._sim.inject_event(cmd[1], cmd[2])
                elif cmd[0] == "gap":
                    self._sim.inject_gap()
                elif cmd[0] == "ok0":
                    self._sim.inject_ok0()
                elif cmd[0] == "reset":
                    self._sim.inject_reset()
                elif cmd[0] == "disconnect":
                    self._sim.inject_disconnect()
                elif cmd[0] == "prob":
                    self._sim.set_prob(bool(cmd[1]))
                elif cmd[0] == "malformed":
                    self._sim.inject_malformed()

    def _on_line(self, raw: bytes) -> None:
        """
        @param raw  One line's bytes without its "\\n".
        """
        item = self._parser.feed(raw.decode("utf-8", errors="replace"))
        if item is None:
            return
        if isinstance(item, LogSample):
            self._batch.append(item)
            return
        if isinstance(item, ProbLine):
            self._prob_batch.append(item)
            return
        self._flush_batch()             # Everything before this item goes first
        if isinstance(item, BootLine):
            self.boot_received.emit(item)
        elif isinstance(item, ConfigLine):
            self.config_received.emit(item)
        elif isinstance(item, HealthLine):
            self.health_received.emit(item)
        elif isinstance(item, FaultLine):
            self.fault_received.emit(item)
        elif isinstance(item, DetLine):
            self.det_received.emit(item)
        elif isinstance(item, SelftestLine):
            self.selftest_received.emit(item)

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
                    if data:
                        buf.extend(data)
                        while True:
                            k = buf.find(b"\n")
                            if k < 0:
                                break
                            line = bytes(buf[:k])
                            del buf[:k + 1]
                            self._on_line(line)
                        if len(buf) > _MAX_PARTIAL_LINE:
                            buf.clear()
                    self._service(time.monotonic())
            except (serial.SerialException, OSError) as exc:
                # Unplugged or reset: a partial line is discarded.
                self._flush_batch()
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
        never slows the device down (L lines are 2.4 ms apart: a late line is
        delivered with the next ones).
        """
        self._sim = SimDevice()
        self.connection_changed.emit(True, "Simulated device (fw %s)" % cfg.SIM_FW)
        due = time.monotonic()
        for delay, item in self._sim.stream():
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
            if isinstance(item, SimDisconnect):
                self._flush_batch()
                self.connection_changed.emit(False, "Simulated USB drop-out")
                self._sleep(item.duration_s)
                if not self._running:
                    return
                self.connection_changed.emit(True, "Simulated device (reconnected)")
                self.reconnected.emit()
                due = time.monotonic()
                continue
            self._on_line(item.encode("utf-8"))
            self._service(time.monotonic())
