"""
File:        snapshot_protocol.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.4.0
Change:      0.4.0 (training-live-inference-handoff.md, request G): the live
             inference lines of firmware-bench 0.1.5-bench: PROB (ProbLine),
             DET (DetLine) and SELFTEST (SelftestLine); HLT gains inferences,
             inf_skips, lat_last_us and lat_max_us; BOOT / CFG model fields are
             read with model_classes(). LineParser counts malformed L, PROB,
             DET, SELFTEST and HLT lines per tag (malformed); malformed_l
             stays.
             0.3.0 (manual-recording-overhaul-handoff.md, request B): the
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
Description: Parses the Serial lines firmware-bench 0.1.5-bench prints
             (serial_out.cpp). Lines starting with '#' and unknown tags (SEG,
             BLE, and any EVT, S or snapshot line from older firmware) are
             ignored. Malformed L, PROB, DET, SELFTEST and HLT lines are
             counted and ignored, never shown as results. Standard
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
# 0.1.5-bench appends the four inference counters; older firmware sends the
# first ten only.
HLT_FIELDS = ("t_ms", "samples", "sample_q_drops", "out_q_drops", "drdy_timeouts",
              "ble_q_drops", "ble_sent", "ble_fails", "serial_drops", "imu_recoveries",
              "inferences", "inf_skips", "lat_last_us", "lat_max_us")

# SELFTEST results (serial_out.cpp selftest_text()).
SELFTEST_RESULTS = ("pass", "fail", "not_run")

# Lines whose malformed copies LineParser counts.
COUNTED_TAGS = ("L", "PROB", "DET", "SELFTEST", "HLT")

# A probability may be off [0, 1] by rounding to 4 decimals, never more.
_PROB_SLACK = 1e-3

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


@dataclass
class ProbLine:
    """A PROB line: one inference on the board (about 2 per second)."""
    n: int                   # Newest sample (L line n) in the window
    probs: tuple             # Floats, one per class, in the model's class order
    lat_us: int              # Time Invoke() took on the board, us
    raw: str


@dataclass
class DetLine:
    """A DET line: the board's own detection (cutoff DET_CONF, 2 s lockout)."""
    n: int                   # Newest sample in the window that fired
    label: str               # Class name, e.g. POTHOLE
    conf: float              # Its probability
    raw: str


@dataclass
class SelftestLine:
    """A SELFTEST line: the boot self-test result, printed once after BOOT."""
    result: str              # One of SELFTEST_RESULTS
    max_diff: float          # Largest difference from the stored outputs
    raw: str


Item = Union[BootLine, ConfigLine, HealthLine, FaultLine, LogSample, ProbLine, DetLine,
             SelftestLine]

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


def model_classes(settings: dict) -> tuple:
    """
    @param settings  BOOT or CFG settings.
    @return The model's class names in output order (classes=A|B|C), or ()
            if the firmware sent none.
    """
    text = settings.get("classes", "")
    return tuple(c for c in text.split("|") if c) if text else ()


def _parse_prob(f: list, line: str) -> Optional[ProbLine]:
    """
    @param f     PROB,n,p1,...,pK,lat_us split on commas.
    @param line  The line.
    @return The ProbLine, or None if malformed.
    """
    if len(f) < 4:
        return None
    n, lat = _to_int(f[1]), _to_int(f[-1])
    probs = [_to_finite_float(x) for x in f[2:-1]]
    if n is None or lat is None or lat < 0 or any(
            p is None or not -_PROB_SLACK <= p <= 1 + _PROB_SLACK for p in probs):
        return None
    return ProbLine(n, tuple(probs), lat, line)


def _parse_det(f: list, line: str) -> Optional[DetLine]:
    """
    @param f     DET,n,label,conf split on commas.
    @param line  The line.
    @return The DetLine, or None if malformed.
    """
    if len(f) != 4:
        return None
    n, conf = _to_int(f[1]), _to_finite_float(f[3])
    label = f[2]
    if n is None or conf is None or not -_PROB_SLACK <= conf <= 1 + _PROB_SLACK or \
            not label or not label.replace("_", "").isalnum():
        return None
    return DetLine(n, label, conf, line)


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
    if tag == "PROB":
        # PROB,n,p_pothole,p_bump,p_none,lat_us (serial_out.cpp, print_msg)
        return _parse_prob(f, line)
    if tag == "DET":
        # DET,n,class,conf
        return _parse_det(f, line)
    if tag == "SELFTEST":
        # SELFTEST,pass|fail|not_run,max_diff
        diff = _to_finite_float(f[2]) if len(f) == 3 else None
        if diff is None or f[1] not in SELFTEST_RESULTS:
            return None
        return SelftestLine(f[1], diff, line)
    return None  # SEG, BLE, and lines from older firmware: not used

# ============================================================================
# Classes: parser
# ============================================================================


class LineParser:
    """Feeds on Serial lines, one at a time, and counts malformed lines."""

    def __init__(self) -> None:
        self.malformed = {tag: 0 for tag in COUNTED_TAGS}   # Ignored, per tag

    @property
    def malformed_l(self) -> int:
        """@return L lines ignored because they did not parse."""
        return self.malformed["L"]

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
        tag = line.split(",", 1)[0]
        if item is None and tag in self.malformed:
            self.malformed[tag] += 1    # Counted and ignored
        return item
