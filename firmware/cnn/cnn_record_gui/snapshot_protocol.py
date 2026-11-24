"""
File:        snapshot_protocol.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B): the
             firmware (0.1.4-bench) streams every sample and detects nothing,
             so this module parses only L, BOOT, CFG, HLT and FLT lines.
             Removed with the snapshots: SnapshotAssembler, Snapshot,
             Rejected, the SNB / SNR / SNE rules and the CRC; EVT and S line
             parsing; snap_drops in HLT (git tag pre-manual-overhaul).
             LineParser replaces the assembler: one line in, at most one item
             out. SampleLine stays as the live view's point type (every
             LIVE_DECIMATE-th L sample). The file keeps its name so its history
             stays in one place.
             0.2.1: CFG lines (ConfigLine, as_boot()).
             0.2.0: L lines (log build), EVT n_trig.
Description: Parses the Serial lines firmware-bench 0.1.4-bench prints
             (serial_out.cpp). Lines starting with '#' and unknown tags (SEG,
             BLE, and any EVT, S or snapshot line from older firmware) are
             ignored. Malformed L lines are counted and ignored. Standard
             library only (no Qt, numpy or pyserial), so it runs in the unit
             tests on any Python 3.11.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Union

# ============================================================================
# Constants
# ============================================================================

# The L line's fields after the tag (serial_out.cpp, print_msg): n, t_us, the
# six raw chip-axis values (accel g, gyro dps, bias NOT removed), the three
# model channels (z_hp g, roll and pitch dps, bias removed), and ok (1 once
# the gyro bias is ready). Clip rows use the same columns.
L_FIELDS = ("n", "t_us", "ax", "ay", "az", "gx", "gy", "gz", "z_hp", "roll", "pitch", "ok")

# Column names of the HLT line, in firmware order (serial_out.cpp print_boot()).
HLT_FIELDS = ("t_ms", "samples", "sample_q_drops", "out_q_drops", "drdy_timeouts",
              "ble_q_drops", "ble_sent", "ble_fails", "serial_drops", "imu_recoveries")

# The live view's point values: chip axes (accel g, gyro dps, bias NOT
# removed), then z_hp (g). Taken from an L sample's raw values and z_hp.
S_CHANNELS = ("ax", "ay", "az", "gx", "gy", "gz", "z_hp")

# ============================================================================
# Functions
# ============================================================================


def _to_int(text: str) -> Optional[int]:
    """
    @param text  Decimal integer text.
    @return The integer, or None if it does not parse.
    """
    try:
        return int(text)
    except ValueError:
        return None


def _to_finite_float(text: str) -> Optional[float]:
    """
    @param text  Decimal number text.
    @return The value, or None if it does not parse or is NaN or infinite.
    """
    try:
        value = float(text)
    except ValueError:
        return None
    return value if math.isfinite(value) else None

# ============================================================================
# Classes: parsed items
# ============================================================================


@dataclass
class BootLine:
    """A BOOT line: key=value settings the firmware ran with."""
    settings: dict           # key -> text
    raw: str


@dataclass
class ConfigLine:
    """A CFG line: the BOOT settings again, repeated every cfg_ms so a PC
    that missed BOOT (it is sent once, right after a reset) still gets them.
    Not a reboot: nothing restarts on a CFG."""
    settings: dict           # key -> text, the same as BOOT's
    raw: str

    def as_boot(self) -> BootLine:
        """
        @return The BOOT line this CFG repeats: the same fields under the tag
                BOOT, which is how clip headers keep settings.
        """
        return BootLine(self.settings, "BOOT" + self.raw[len("CFG"):])


@dataclass
class HealthLine:
    """An HLT line: health counters."""
    fields: dict             # HLT_FIELDS name -> int, for the fields present
    raw: str


@dataclass
class FaultLine:
    """An FLT line: fault code and name."""
    code: str
    name: str
    raw: str


@dataclass
class SampleLine:
    """One point of the live IMU view (every LIVE_DECIMATE-th L sample).
    Display only, never saved."""
    n: int                   # Device sample count since boot
    t_us: int                # Device time since boot, us
    values: tuple            # Floats in S_CHANNELS order


@dataclass
class LogSample:
    """An L line: one IMU sample. Also one row of a clip. text keeps every
    field exactly as sent, so clips copy it without re-formatting."""
    n: int                   # Device sample count since boot
    t_us: int                # Device time since boot, us
    ok: bool                 # Gyro bias ready
    raw: tuple               # ax, ay, az (g), gx, gy, gz (dps, bias in) as floats
    model: tuple             # z_hp (g), roll, pitch (dps, bias removed) as floats
    text: tuple              # The 12 L_FIELDS as text, exactly as sent

    def row_text(self) -> str:
        """
        @return The clip row: the 12 fields as sent, comma-separated.
        """
        return ",".join(self.text)


Item = Union[BootLine, ConfigLine, HealthLine, FaultLine, LogSample]

# ============================================================================
# Functions: line parsers
# ============================================================================


def parse_l_fields(fields: list) -> Optional[LogSample]:
    """
    Parses the 12 fields of an L line after its tag (also one clip row).
    @param fields  The fields as text, in L_FIELDS order.
    @return The sample, or None if it is malformed (wrong count, a number
            that does not parse or is not finite, ok not 0 or 1).
    """
    if len(fields) != len(L_FIELDS):
        return None
    n, t_us = _to_int(fields[0]), _to_int(fields[1])
    vals = [_to_finite_float(x) for x in fields[2:11]]
    if n is None or t_us is None or any(v is None for v in vals) or fields[11] not in ("0", "1"):
        return None
    return LogSample(n, t_us, fields[11] == "1", tuple(vals[:6]), tuple(vals[6:9]),
                     tuple(fields))


def _settings(fields: list) -> dict:
    """
    @param fields  A BOOT or CFG line's fields after the tag.
    @return Its key=value settings (fields without '=' are skipped).
    """
    settings = {}
    for part in fields:
        key, sep, value = part.partition("=")
        if sep:
            settings[key] = value
    return settings


def parse_line(line: str) -> Optional[Item]:
    """
    Parses one line.
    @param line  The line, line ending already removed.
    @return A LogSample, BootLine, ConfigLine, HealthLine or FaultLine, or
            None (malformed, a comment, or a tag the GUI does not use).
    """
    f = line.split(",")
    tag = f[0]
    if tag == "L":
        # L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok (serial_out.cpp, print_msg)
        return parse_l_fields(f[1:])
    if tag == "BOOT":
        return BootLine(_settings(f[1:]), line)
    if tag == "CFG":
        return ConfigLine(_settings(f[1:]), line)
    if tag == "HLT":
        nums = [_to_int(x) for x in f[1:]]
        if not nums or any(n is None for n in nums):
            return None
        return HealthLine(dict(zip(HLT_FIELDS, nums)), line)
    if tag == "FLT":
        return FaultLine(f[1] if len(f) > 1 else "", f[2] if len(f) > 2 else "", line)
    return None  # SEG, BLE, and lines from older firmware: not used

# ============================================================================
# Classes: parser
# ============================================================================


class LineParser:
    """Feeds on Serial lines, one at a time, and counts malformed L lines."""

    def __init__(self) -> None:
        self.malformed_l = 0     # L lines ignored because they did not parse

    def feed(self, line: str) -> Optional[Item]:
        """
        Processes one line.
        @param line  One Serial line; a trailing line ending is removed.
        @return The parsed item, or None (empty, comment, unused or malformed).
        """
        line = line.rstrip("\r\n")
        if not line or line.startswith("#"):
            return None
        item = parse_line(line)
        if item is None and line.split(",", 1)[0] == "L":
            self.malformed_l += 1    # Counted and ignored
        return item
