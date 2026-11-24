"""
File:        snapshot_protocol.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.2.0
Change:      0.2.0 (drive-log-manual-record-handoff.md v0.2, request 5):
             parses the log build's L lines (every sample, text kept so it
             can be copied into takes unchanged) and counts malformed ones;
             EVT gains n_trig, its 13th field (firmware-bench 0.1.3-bench,
             every build); an L line inside a snapshot block interrupts it,
             like any other known line.
Description: Parses the Serial lines from firmware-bench 0.1.3-bench
             (cnn-record-train-handoff.md v0.4, "Serial snapshot protocol";
             drive-log-manual-record-handoff.md v0.2, request 4).
             SnapshotAssembler turns SNB / SNR / SNE lines into one validated
             Snapshot, or one Rejected with the reason, and parses BOOT, EVT,
             HLT, FLT, the S sample lines (live IMU view) and the log build's
             L lines. parse_other_line() and parse_l_fields() are also used by
             the window cutter. Standard library only (no Qt, numpy or
             pyserial), so it runs in the unit tests on any Python 3.11.

             Rules (handoff): lines starting with '#' and unknown tags are
             ignored. A snapshot missing its SNE, with a gap in i, with the
             wrong row count, or failing the CRC is rejected and never saved.
             The firmware sends a snapshot as one uninterrupted block, so any
             other known line between SNB and SNE means the device abandoned
             it (TX timeout) or reset: that also rejects it. Each lost
             snapshot is counted once.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import binascii
import math
from dataclasses import dataclass, field
from typing import Optional, Union

# ============================================================================
# Constants
# ============================================================================

# Column names of the positional lines, in firmware order (serial_out.cpp,
# print_boot() headers). HLT has 10 fields in product and log builds and 11
# (with snap_drops) in record builds. EVT's n_trig (the trigger's sample
# count) is last, so older lines without it still parse.
EVT_FIELDS = ("seq", "n_peak", "t_us_peak", "class", "peak_z_g", "peak_roll_dps",
              "peak_pitch_dps", "t_ms", "synced", "pitch_seq", "basis", "n_trig")
HLT_FIELDS = ("t_ms", "samples", "sample_q_drops", "out_q_drops", "drdy_timeouts",
              "ble_q_drops", "ble_sent", "ble_fails", "serial_drops", "imu_recoveries",
              "snap_drops")
# The S line's value columns after n and t_us: chip axes, accel in g, gyro in
# dps (settle-time bias NOT removed), then the high-passed vertical accel in g.
S_CHANNELS = ("ax", "ay", "az", "gx", "gy", "gz", "z_hp")

# The L line's fields after the tag (log build, serial_out.cpp print_msg):
# n, t_us, the six raw chip-axis values (accel g, gyro dps, bias NOT
# removed), the three model channels exactly as snapshot rows carry them
# (z_hp g, roll and pitch dps, bias removed), and ok (1 once the gyro bias is
# ready). Raw manual recordings use the same columns.
L_FIELDS = ("n", "t_us", "ax", "ay", "az", "gx", "gy", "gz", "z_hp", "roll", "pitch", "ok")

# Known tags that are not part of a snapshot. Seeing one between SNB and SNE
# means the block was interrupted.
OTHER_KNOWN_TAGS = ("BOOT", "S", "L", "SEG", "EVT", "HLT", "FLT", "BLE")

# ============================================================================
# Functions
# ============================================================================


def crc16_ccitt_false(data: bytes, crc: int = 0xFFFF) -> int:
    """
    CRC-16/CCITT-FALSE: polynomial 0x1021, initial value 0xFFFF, no reflection,
    no final XOR. binascii.crc_hqx computes exactly this variant (handoff,
    checked against the standard check value below).
    @param data  Bytes to add.
    @param crc   CRC so far; 0xFFFF to start.
    @return The updated CRC, 0..0xFFFF.
    """
    return binascii.crc_hqx(data, crc)


# Standard check value: ASCII "123456789" gives 0x29B1. Refuse to run if the
# CRC is wrong, rather than reject every snapshot.
if crc16_ccitt_false(b"123456789") != 0x29B1:
    raise RuntimeError("binascii.crc_hqx does not compute CRC-16/CCITT-FALSE here")


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
class Snapshot:
    """One complete, validated snapshot (SNB + SNR rows + SNE)."""
    seq: int                 # Matches the EVT line of the same event
    trig_idx: int            # Device sample count at the threshold crossing
    rate_hz: int
    n_pre: int               # Rows before the trigger; row n_pre is the trigger sample
    n_post: int
    n_rows: int
    channels: str            # e.g. "az_hp/gx/gy"
    fw: str
    crc16: str               # As printed in SNE (4 hex digits)
    rows: list = field(default_factory=list)    # (c0, c1, c2) text exactly as sent
    values: list = field(default_factory=list)  # The same rows as floats, for plotting


@dataclass
class Rejected:
    """A snapshot that was started (or partly seen) but cannot be saved."""
    reason: str
    seq: Optional[int] = None


@dataclass
class EventLine:
    """An EVT line: the rule-based class and peaks for one event."""
    seq: int
    fields: dict             # EVT_FIELDS name -> text, for the fields present
    raw: str


@dataclass
class BootLine:
    """A BOOT line: key=value settings the firmware ran with."""
    settings: dict           # key -> text
    raw: str


@dataclass
class HealthLine:
    """An HLT line: health counters (snap_drops only in record builds)."""
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
    """An S line: one IMU sample, every PRINT_EVERY_N-th (display only, never
    saved: takes come from snapshots)."""
    n: int                   # Device sample count since boot
    t_us: int                # Device time since boot, us
    values: tuple            # Floats in S_CHANNELS order


@dataclass
class LogSample:
    """An L line (log build): one IMU sample, every sample. Also one row of a
    raw manual recording. text keeps every field exactly as sent, so takes
    cut from it copy the model channels without re-formatting them."""
    n: int                   # Device sample count since boot
    t_us: int                # Device time since boot, us
    ok: bool                 # Gyro bias ready (rows with ok False never go into takes)
    raw: tuple               # ax, ay, az (g), gx, gy, gz (dps, bias in) as floats
    model: tuple             # z_hp (g), roll, pitch (dps, bias removed) as floats
    text: tuple              # The 12 L_FIELDS as text, exactly as sent

    def model_text(self) -> tuple:
        """
        @return z_hp, roll, pitch as text, exactly as sent (a take row).
        """
        return self.text[8:11]


Item = Union[Snapshot, Rejected, EventLine, BootLine, HealthLine, FaultLine, SampleLine,
             LogSample]

# ============================================================================
# Functions: line parsers (also used by the window cutter)
# ============================================================================


def parse_l_fields(fields: list) -> Optional[LogSample]:
    """
    Parses the 12 fields of an L line after its tag, or one row of a raw
    manual recording.
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


def parse_other_line(line: str) -> Optional[Item]:
    """
    Parses one line that is not part of a snapshot block.
    @param line  The line, line ending already removed.
    @return An EventLine, BootLine, HealthLine, FaultLine, SampleLine or
            LogSample, or None (malformed, a comment, or a tag the GUI does
            not use: SEG, BLE, snapshot lines).
    """
    f = line.split(",")
    tag = f[0]
    if tag == "L":
        # L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok (serial_out.cpp, print_msg)
        return parse_l_fields(f[1:])
    if tag == "S":
        # S,n,t_us,ax,ay,az,gx,gy,gz,z_hp (serial_out.cpp, print_msg)
        if len(f) != 3 + len(S_CHANNELS):
            return None
        n, t_us = _to_int(f[1]), _to_int(f[2])
        vals = tuple(_to_finite_float(x) for x in f[3:])
        if n is None or t_us is None or any(v is None for v in vals):
            return None
        return SampleLine(n, t_us, vals)
    if tag == "EVT":
        seq = _to_int(f[1]) if len(f) > 1 else None
        if seq is None:
            return None
        return EventLine(seq, dict(zip(EVT_FIELDS, f[1:])), line)
    if tag == "BOOT":
        settings = {}
        for part in f[1:]:
            key, sep, value = part.partition("=")
            if sep:
                settings[key] = value
        return BootLine(settings, line)
    if tag == "HLT":
        nums = [_to_int(x) for x in f[1:]]
        if not nums or any(n is None for n in nums):
            return None
        return HealthLine(dict(zip(HLT_FIELDS, nums)), line)
    if tag == "FLT":
        return FaultLine(f[1] if len(f) > 1 else "", f[2] if len(f) > 2 else "", line)
    return None  # SEG, BLE: not used by the GUI

# ============================================================================
# Classes: assembler
# ============================================================================


class SnapshotAssembler:
    """
    Feeds on Serial lines, one at a time, and returns what each completes.
    States: idle; active (inside SNB..SNE); orphan (SNR rows seen without an
    SNB, e.g. the port opened mid-snapshot, not yet counted); skip (rows of a
    snapshot already counted as rejected, ignored until its SNE).
    """

    def __init__(self, stall_timeout_s: float, max_rows: int) -> None:
        """
        @param stall_timeout_s  An active or orphan snapshot with no new line
                                for this long is rejected (missing SNE).
        @param max_rows         Largest n_rows accepted in an SNB.
        """
        self._stall_timeout_s = stall_timeout_s
        self._max_rows = max_rows
        self._state = "idle"
        self._cur: Optional[dict] = None   # The snapshot being assembled
        self._last_line_t = 0.0            # Time of its last SNB / SNR line
        self.malformed_l = 0               # L lines ignored because they did not parse

    def feed(self, line: str, now: float) -> list:
        """
        Processes one line.
        @param line  One Serial line; a trailing line ending is removed.
        @param now   Monotonic time in seconds (for the stall timeout).
        @return Items completed by this line: Snapshot, Rejected, EventLine,
                BootLine, HealthLine, FaultLine, SampleLine, LogSample.
                Usually empty or one item.
        """
        line = line.rstrip("\r\n")
        if not line or line.startswith("#"):
            return []  # Empty line or comment / column header
        tag = line.split(",", 1)[0]

        if tag == "SNB":
            out = self._close_open("missing SNE (new SNB arrived)")
            out.extend(self._start(line, now))
            return out
        if tag == "SNR":
            return self._row(line, now)
        if tag == "SNE":
            return self._end(line)
        if tag in OTHER_KNOWN_TAGS:
            # The firmware never interleaves lines with a snapshot, so this
            # one means the snapshot was abandoned on the device, or the
            # device reset (BOOT).
            out = self._close_open("missing SNE (interrupted by %s)" % tag) \
                if self._state != "idle" else []
            item = parse_other_line(line)
            if item is not None:
                out.append(item)
            elif tag == "L":
                self.malformed_l += 1   # Counted and ignored (handoff, request 5)
            return out
        return []  # Unknown tag: ignored, and does not interrupt a snapshot

    def check_stall(self, now: float) -> list:
        """
        Rejects a snapshot that stopped arriving (no SNE).
        @param now  Monotonic time in seconds.
        @return A one-item list with the Rejected, or empty.
        """
        if self._state in ("active", "orphan", "skip") and \
                now - self._last_line_t >= self._stall_timeout_s:
            return self._close_open("missing SNE (timed out)")
        return []

    def abort(self, reason: str) -> list:
        """
        Discards a partly received snapshot, e.g. when the port is lost.
        @param reason  Why, for the Rejected item.
        @return A one-item list with the Rejected, or empty if none was open.
        """
        return self._close_open(reason)

    # ---------------------------------------------------------------- helpers

    def _close_open(self, reason: str) -> list:
        """
        Ends whatever snapshot is open. Active and orphan snapshots have not
        been counted yet, so they become one Rejected; skip was counted.
        @param reason  Why it is rejected.
        @return A list with at most one Rejected.
        """
        out = []
        if self._state == "active":
            out.append(Rejected(self._cur.get("bad") or reason, self._cur["seq"]))
        elif self._state == "orphan":
            out.append(Rejected("missing SNB (rows arrived without a start)"))
        self._state = "idle"
        self._cur = None
        return out

    def _start(self, line: str, now: float) -> list:
        """
        Starts a snapshot from an SNB line:
        SNB,seq,trig_idx,rate_hz,n_pre,n_post,n_rows,channels,fw
        @param line  The SNB line.
        @param now   Monotonic time.
        @return Empty, or a Rejected if the SNB is malformed (its rows are
                then skipped without being counted again).
        """
        f = line.split(",")
        nums = [_to_int(x) for x in f[1:7]] if len(f) == 9 else [None]
        if len(f) != 9 or any(n is None for n in nums):
            self._state = "skip"
            self._last_line_t = now
            return [Rejected("malformed SNB")]
        seq, trig_idx, rate_hz, n_pre, n_post, n_rows = nums
        if rate_hz <= 0 or n_pre < 0 or n_post < 1 or n_rows != n_pre + n_post \
                or n_rows > self._max_rows:
            self._state = "skip"
            self._last_line_t = now
            return [Rejected("inconsistent SNB settings", seq)]
        self._cur = {"seq": seq, "trig_idx": trig_idx, "rate_hz": rate_hz,
                     "n_pre": n_pre, "n_post": n_post, "n_rows": n_rows,
                     "channels": f[7], "fw": f[8], "rows": [], "values": [],
                     "next_i": 0, "crc": 0xFFFF, "bad": None}
        self._state = "active"
        self._last_line_t = now
        return []

    def _row(self, line: str, now: float) -> list:
        """
        Adds one SNR line (SNR,i,c0,c1,c2). The CRC runs over the exact bytes
        of every SNR line, in order, without the line ending. The first
        problem found is kept as the rejection reason; the snapshot is
        rejected at its SNE (or when it ends some other way).
        @param line  The SNR line, line ending already removed.
        @param now   Monotonic time.
        @return Always empty.
        """
        if self._state in ("idle", "orphan"):
            self._state = "orphan"     # Rows without a start; counted once later
            self._last_line_t = now
            return []
        self._last_line_t = now
        if self._state == "skip":
            return []
        cur = self._cur
        cur["crc"] = crc16_ccitt_false(line.encode("utf-8"), cur["crc"])
        if cur["bad"]:
            return []
        f = line.split(",")
        i = _to_int(f[1]) if len(f) == 5 else None
        vals = [_to_finite_float(x) for x in f[2:5]] if len(f) == 5 else [None]
        if i is None or any(v is None for v in vals):
            cur["bad"] = "malformed SNR"
        elif i != cur["next_i"]:
            cur["bad"] = "gap in i (expected %d, got %d)" % (cur["next_i"], i)
        elif len(cur["rows"]) >= cur["n_rows"]:
            cur["bad"] = "more rows than n_rows"
        else:
            cur["rows"].append((f[2], f[3], f[4]))
            cur["values"].append(tuple(vals))
            cur["next_i"] = i + 1
        return []

    def _end(self, line: str) -> list:
        """
        Finishes a snapshot on SNE,seq,n_rows,crc16 and validates it.
        @param line  The SNE line.
        @return A Snapshot, a Rejected, or empty (end of a skipped block).
        """
        if self._state == "skip":
            self._state = "idle"
            return []
        if self._state in ("idle", "orphan"):
            self._state = "idle"
            return [Rejected("missing SNB (rows arrived without a start)")]
        cur = self._cur
        self._state = "idle"
        self._cur = None
        f = line.split(",")
        seq = _to_int(f[1]) if len(f) == 4 else None
        n_rows = _to_int(f[2]) if len(f) == 4 else None
        try:
            crc = int(f[3], 16) if len(f) == 4 and len(f[3]) == 4 else None
        except ValueError:
            crc = None
        if cur["bad"]:
            return [Rejected(cur["bad"], cur["seq"])]
        if seq is None or n_rows is None or crc is None:
            return [Rejected("malformed SNE", cur["seq"])]
        if seq != cur["seq"]:
            return [Rejected("SNE seq %d does not match SNB seq %d" % (seq, cur["seq"]),
                             cur["seq"])]
        if n_rows != cur["n_rows"] or len(cur["rows"]) != cur["n_rows"]:
            return [Rejected("wrong row count (SNB %d, SNE %d, received %d)"
                             % (cur["n_rows"], n_rows, len(cur["rows"])), cur["seq"])]
        if crc != cur["crc"]:
            return [Rejected("CRC mismatch (sent %04X, computed %04X)" % (crc, cur["crc"]),
                             cur["seq"])]
        return [Snapshot(seq=cur["seq"], trig_idx=cur["trig_idx"], rate_hz=cur["rate_hz"],
                         n_pre=cur["n_pre"], n_post=cur["n_post"], n_rows=cur["n_rows"],
                         channels=cur["channels"], fw=cur["fw"], crc16="%04X" % crc,
                         rows=cur["rows"], values=cur["values"])]
