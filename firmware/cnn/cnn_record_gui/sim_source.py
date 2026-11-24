"""
File:        sim_source.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.2.0
Change:      0.2.0 (drive-log-manual-record-handoff.md v0.2, request 5,
             "Simulated device"): firmware-bench 0.1.3-bench. EVT gains n_trig
             in both builds. A log build (build="log"): BOOT with log=1, the
             #L header, SIM_SETTLE_S of L lines with ok=0 after every boot,
             then an L line every sample (no S lines, no snapshots), the
             10-field HLT, and injected jumps in n (SIM_GAP_EVERY), resets and
             USB drop-outs. Its test mode (with_snapshots=True) also sends the
             record build's snapshot for each event, so a test can check that
             a window cut from the L lines equals the snapshot (CUT-1). Every
             event's shape now crosses the threshold at the trigger row
             (false triggers too), as a real trigger does.
             Bench mapping (2026-11-23): chip Y points forward, so S and L
             lines carry pitch in gx and roll in gy, and a pothole's first
             pitch lobe is negative (PITCH_NOSE_DOWN_SIGN -1). CFG line
             (2026-11-23): every CFG_PERIOD_S the BOOT fields go out again as
             a CFG line, and BOOT gains cfg_ms, as the firmware now does.
Adapted from: the simulated-data option (generate_random_data) of
             Python/real_time_graph.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Only the idea
             is kept: instead of random 50 Hz points it now emits fake
             snapshots in the record firmware's line format.
Description: A simulated device, so the GUI can be built and tested without
             hardware (handoff, "Simulated source"). As the record build it
             yields the same lines firmware-bench 0.1.3-bench prints with
             RECORD_MODE 1: BOOT and the '#' headers, HLT once a second, an S
             line every SIM_PRINT_EVERY samples (noise between events, each
             event's shape while it happens: the live IMU view), and per
             event, when its window closes, an EVT plus an SNB / SNR / SNE
             snapshot with a real CRC-16, in either order. Fault injection
             (cnn_config SIM_*):
             missing row, bad CRC, wrong length, missing SNE, snapshot dropped
             on the device (EVT only, snap_drops counted), device reset (seq
             back to 0), and a USB drop-out in the middle of a snapshot. As
             the log build (LOG_MODE 1) it sends L lines instead (see Change).
             Its fw starts with "sim-" so its takes cannot pass as real.
             Standard library only.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Iterator

import cnn_config as cfg
from snapshot_protocol import crc16_ccitt_false

# ============================================================================
# Constants
# ============================================================================

# Fault kinds, in the order they are injected.
FAULT_KINDS = ("missing_row", "bad_crc", "wrong_length", "missing_sne", "device_drop")

# Gyro bias in the S lines (chip X, Y, Z, dps). The firmware removes the bias
# only from what the trigger and the snapshots see, not from S lines.
_SIM_GYRO_BIAS = (0.4, -0.3, 0.2)

# firmware-bench's PITCH_NOSE_DOWN_SIGN (config.h). Bench mapping: chip Y
# points forward, so pitch is chip gx (roll is chip gy) and nose down is a
# negative pitch rate: a pothole's first pitch lobe is negative.
_SIM_NOSE_DOWN_SIGN = -1.0

# ============================================================================
# Classes
# ============================================================================


@dataclass
class SimDisconnect:
    """Marker in the stream: USB is gone for duration_s, then comes back."""
    duration_s: float


class SimDevice:
    """Generates the simulated device's output, forever."""

    def __init__(self, seed: int = cfg.SIM_SEED,
                 event_period_s: float = cfg.SIM_EVENT_PERIOD_S,
                 bad_every: int = cfg.SIM_BAD_EVERY,
                 reset_every: int = cfg.SIM_RESET_EVERY,
                 disconnect_every: int = cfg.SIM_DISCONNECT_EVERY,
                 disconnect_s: float = cfg.SIM_DISCONNECT_S,
                 build: str = "record", with_snapshots: bool = False,
                 gap_every: int = cfg.SIM_GAP_EVERY,
                 gap_samples: int = cfg.SIM_GAP_SAMPLES,
                 settle_s: float = cfg.SIM_SETTLE_S) -> None:
        """
        @param seed              Random seed (same run every time).
        @param event_period_s    Seconds between events.
        @param bad_every         Every n-th event is faulty (0 = never; record build only).
        @param reset_every       Every n-th event follows a device reset (0 = never).
        @param disconnect_every  Every n-th event loses USB (0 = never): mid-snapshot
                                 in the record build, after the event in the log build.
        @param disconnect_s      How long USB stays away.
        @param build             "record" (RECORD_MODE 1) or "log" (LOG_MODE 1).
        @param with_snapshots    Log build test mode: also send each event's snapshot.
        @param gap_every         Log build: every n-th event, L lines are lost before it.
        @param gap_samples       How many.
        @param settle_s          Log build: seconds of ok=0 L lines after each boot.
        """
        if build not in ("record", "log"):
            raise ValueError("build must be 'record' or 'log', not %r" % build)
        self._log = build == "log"
        self._with_snapshots = with_snapshots
        self._gap_every = gap_every
        self._gap_samples = gap_samples
        self._settle_n = int(round(settle_s * cfg.SIM_RATE_HZ))
        self._drop = 0          # L lines still to drop (an injected jump in n)
        self.gaps = []          # (first lost n, count) of every injected jump, for tests
        self._rng = random.Random(seed)
        # Noise between events has its own generator, so the events (class,
        # shape, EVT order) depend only on the seed.
        self._noise = random.Random(seed + 1)
        self._period = event_period_s
        self._bad_every = bad_every
        self._reset_every = reset_every
        self._disconnect_every = disconnect_every
        self._disconnect_s = disconnect_s
        self._t = 0.0           # Device time since boot, s
        self._n = 0             # Device sample count since boot
        self._next_hlt_t = 1.0  # Device time of the next HLT, s
        self._next_cfg_t = cfg.CFG_PERIOD_S  # Device time of the next CFG, s
        self._cfg_line = ""     # The CFG line: this boot's BOOT fields under the tag CFG
        self._pending = 0.0     # Device time not yet given out as a delay, s
        self._seq = 0
        self._snap_drops = 0
        self.plan = []          # (event number, seq, kind) for every event, for tests

    def stream(self) -> Iterator[tuple]:
        """
        @return An endless iterator of (delay_s, item): wait delay_s, then
                deliver item, which is a line (str, no line ending) or a
                SimDisconnect.
        """
        yield from self._boot()
        # An event takes the snapshot's length (pre-trigger samples, then the
        # window); the rest of the period is quiet.
        quiet_n = max(0, int(self._period * cfg.SIM_RATE_HZ) - cfg.SIM_N_PRE - cfg.SIM_N_POST)
        k = 0
        while True:
            k += 1
            if self._log and self._gap_every and k % self._gap_every == 0 \
                    and quiet_n > 2 * self._gap_samples:
                # Log build: L lines lost in the middle of the quiet stretch,
                # well clear of the event's window.
                half = quiet_n // 2
                yield from self._samples(self._noise_rows(half))
                self._drop = self._gap_samples
                yield from self._samples(self._noise_rows(quiet_n - half))
            else:
                yield from self._samples(self._noise_rows(quiet_n))

            if self._reset_every and k % self._reset_every == 0:
                yield from self._boot()

            kind = "ok"
            if not self._log and self._bad_every and k % self._bad_every == 0:
                kind = FAULT_KINDS[(k // self._bad_every - 1) % len(FAULT_KINDS)]
            if self._disconnect_every and k % self._disconnect_every == 0:
                kind = "disconnect"
            self.plan.append((k, self._seq, kind))
            yield from self._event(kind)

    # ---------------------------------------------------------------- helpers

    def _boot(self) -> Iterator[tuple]:
        """
        A (re)boot: BOOT and the column headers; seq and the clock restart.
        The log build then sends SIM_SETTLE_S of ok=0 samples (gyro bias not
        ready yet). Every CFG_PERIOD_S of device time after this, _samples()
        repeats the BOOT fields as a CFG line.
        @return (delay, line) pairs.
        """
        self._t = 0.0
        self._n = 0
        self._next_hlt_t = 1.0
        self._next_cfg_t = cfg.CFG_PERIOD_S
        self._seq = 0
        self._snap_drops = 0
        self._drop = 0
        common = ("BOOT,fw=%s,odr=416,acc_fs=16,gyr_fs=500,thr_z=1.000,thr_roll=60.0,"
                  "thr_pitch=60.0,lockout_ms=1250,rearm_frac=0.50,rearm_hold_ms=200,"
                  "hpf_hz=0.50,print_every=%d,settle_ms=2000,cfg_ms=%d,ble=RoadQuality"
                  % (cfg.SIM_FW, cfg.SIM_PRINT_EVERY, int(round(cfg.CFG_PERIOD_S * 1000))))
        evt_header = ("#EVT,seq,n_peak,t_us_peak,class,peak_z_g,peak_roll_dps,"
                      "peak_pitch_dps,t_ms,synced,pitch_seq,basis,n_trig")
        hlt_header = ("#HLT,t_ms,samples,sample_q_drops,out_q_drops,drdy_timeouts,"
                      "ble_q_drops,ble_sent,ble_fails,serial_drops,imu_recoveries")
        if self._log:
            boot = common + (",rec=0,log=1,l_rate=%d,l_dec=4,log_tx_buf=32768,"
                             "log_out_q=256,log_perf_ms=10000" % cfg.SIM_RATE_HZ)
            headers = ("#L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok",
                       "#LPERF,t_ms,lines,avg_us,max_us,heap_min", evt_header, hlt_header)
        else:
            boot = common + (",rec=1,snap_rate=%d,snap_pre=%d,snap_post=%d,snap_rows=%d,"
                             "snap_ch=%s" % (cfg.SIM_RATE_HZ, cfg.SIM_N_PRE, cfg.SIM_N_POST,
                                             cfg.SIM_N_PRE + cfg.SIM_N_POST, cfg.SIM_CHANNELS))
            headers = ("#S,n,t_us,ax,ay,az,gx,gy,gz,z_hp", evt_header,
                       hlt_header + ",snap_drops",
                       "#SNB,seq,trig_idx,rate_hz,n_pre,n_post,n_rows,channels,fw",
                       "#SNR,i,c0,c1,c2", "#SNE,seq,n_rows,crc16")
        self._cfg_line = "CFG" + boot[len("BOOT"):]
        yield 0.0, boot
        for header in headers:
            yield 0.0, header
        if self._log and self._settle_n:
            yield from self._samples(self._noise_rows(self._settle_n), ok=False)

    def _hlt(self) -> str:
        """
        @return An HLT line: 11 fields (with snap_drops) in the record build,
                10 in the log build.
        """
        t_ms = int(self._t * 1000)
        line = "HLT,%d,%d,0,0,0,0,%d,0,0,0" % (t_ms, self._n, int(self._t))
        return line if self._log else line + ",%d" % self._snap_drops

    def _l_line(self, z: float, roll: float, pitch: float, ok: bool) -> str:
        """
        One L line as serial_out.cpp prints it (LOG_DECIMALS = 4): chip axes
        with gravity on az and the gyro bias in, then the model channels
        (bias removed), then ok.
        @param z      z_hp, g.
        @param roll   Roll rate, dps (bench mapping: chip Y gyro).
        @param pitch  Pitch rate, dps (bench mapping: chip X gyro).
        @param ok     Gyro bias ready.
        @return The line.
        """
        nz = self._noise
        bx, by, bz = _SIM_GYRO_BIAS
        # Raw columns in chip order: gx = pitch, gy = roll.
        return "L,%d,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d" % (
            self._n, int(round(self._t * 1e6)), nz.gauss(0.0, 0.01), nz.gauss(0.0, 0.01),
            1.0 + z, pitch + bx, roll + by, nz.gauss(0.0, 1.0) + bz, z, roll, pitch,
            1 if ok else 0)

    def _noise_rows(self, count: int) -> list:
        """
        @param count  Number of samples.
        @return count (z_hp, roll, pitch) rows of sensor noise, nothing happening.
        """
        nz = self._noise
        return [(nz.gauss(0.0, 0.02), nz.gauss(0.0, 1.5), nz.gauss(0.0, 1.5))
                for _ in range(count)]

    def _samples(self, rows: list, ok: bool = True) -> Iterator[tuple]:
        """
        Runs device time forward one sample per row, as the firmware does: an
        S line every SIM_PRINT_EVERY-th sample (record build) or an L line
        every sample (log build; skipped while an injected jump is being
        dropped), an HLT each time device time passes a whole second, and a
        CFG every CFG_PERIOD_S. Never inside a snapshot: those go out after
        the samples, as the firmware sends them between items.
        @param rows  (z_hp, roll, pitch) per sample: noise, or an event's shape.
        @param ok    The L lines' ok field (False during settle).
        @return (delay, line) pairs; each delay is the device time since the
                line before.
        """
        dt = 1.0 / cfg.SIM_RATE_HZ
        nz = self._noise
        bx, by, bz = _SIM_GYRO_BIAS
        for z, roll, pitch in rows:
            self._n += 1
            self._t = self._n / cfg.SIM_RATE_HZ
            self._pending += dt
            if self._log:
                if self._drop > 0:
                    if self._drop == self._gap_samples:
                        self.gaps.append((self._n, self._gap_samples))
                    self._drop -= 1          # Lost on the way: n jumps
                else:
                    yield self._pending, self._l_line(z, roll, pitch, ok)
                    self._pending = 0.0
            elif self._n % cfg.SIM_PRINT_EVERY == 0:
                # Chip axes, as serial_out.cpp prints them: gravity on az, the
                # gyro with its bias (gx = pitch, gy = roll), then z_hp.
                yield self._pending, ("S,%d,%d,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%.4f" % (
                    self._n, int(round(self._t * 1e6)), nz.gauss(0.0, 0.01),
                    nz.gauss(0.0, 0.01), 1.0 + z, pitch + bx, roll + by,
                    nz.gauss(0.0, 1.0) + bz, z))
                self._pending = 0.0
            if self._t >= self._next_hlt_t:
                self._next_hlt_t += 1.0
                yield self._pending, self._hlt()
                self._pending = 0.0
            if self._t >= self._next_cfg_t:
                self._next_cfg_t += cfg.CFG_PERIOD_S
                yield self._pending, self._cfg_line
                self._pending = 0.0

    def _rows(self, true_class: str) -> list:
        """
        One snapshot's rows, shaped like the class (pothole: nose down then
        up and a drop; speed bump: the reverse; false trigger: a short
        vertical spike), with noise. Row n_pre is where the shape starts.
        @param true_class  "POTHOLE", "SPEED_BUMP" or "FALSE_TRIGGER".
        @return List of (z_hp, roll, pitch) floats.
        """
        rng = self._rng
        sign = {"POTHOLE": 1.0, "SPEED_BUMP": -1.0}.get(true_class, 0.0)
        amp = rng.uniform(1.2, 3.0)
        rows = []
        for i in range(cfg.SIM_N_PRE + cfg.SIM_N_POST):
            t = (i - cfg.SIM_N_PRE) / cfg.SIM_RATE_HZ  # s from the trigger
            z = rng.gauss(0.0, 0.02)
            roll = rng.gauss(0.0, 1.5)
            pitch = rng.gauss(0.0, 1.5)
            if t >= 0.0:
                ring = math.exp(-t / 0.12)
                if sign == 0.0:
                    z += amp * math.exp(-t / 0.03) * math.sin(2 * math.pi * 30 * t)
                else:
                    z += -sign * amp * ring * math.sin(2 * math.pi * 4 * t)
                    # sign is +1 for nose down first; the firmware's sign turns
                    # it into a pitch rate.
                    pitch += _SIM_NOSE_DOWN_SIGN * sign * 120 * ring * math.sin(2 * math.pi * 3 * t)
                    roll += 25 * ring * math.sin(2 * math.pi * 5 * t)
                if t < 0.003:
                    # Cross the threshold at the trigger row, as a real trigger does.
                    z = (-sign if sign else 1.0) * max(abs(z), 1.05)
            rows.append((z, roll, pitch))
        return rows

    def _event(self, kind: str) -> Iterator[tuple]:
        """
        One event: the S lines while it happens, then (when its window
        closes) its EVT and snapshot lines, with the fault kind applied.
        @param kind  "ok", one of FAULT_KINDS, or "disconnect".
        @return (delay, item) pairs.
        """
        rng = self._rng
        true_class = rng.choice(cfg.CLASSES)
        rule = {"POTHOLE": "POTHOLE", "SPEED_BUMP": "BUMP"}.get(true_class) or \
            rng.choice(("POTHOLE", "BUMP"))
        rows = self._rows(true_class)
        yield from self._samples(rows[:cfg.SIM_N_PRE])   # Before the trigger
        seq = self._seq
        self._seq += 1                     # Every window uses a seq, dropped or not
        trig_idx = self._n + 1             # The next sample (row n_pre) crosses
        n_peak = trig_idx + 40
        # n_trig (the trigger's sample count) is the 13th field in every build.
        evt = "EVT,%d,%d,%d,%s,%.3f,%.1f,%.1f,%d,0,%s,pitch,%d" % (
            seq, n_peak, n_peak * 1000000 // cfg.SIM_RATE_HZ, rule, rng.uniform(1.0, 3.0),
            rng.uniform(10, 80), rng.uniform(60, 400), n_peak * 1000 // cfg.SIM_RATE_HZ,
            "DU" if rule == "POTHOLE" else "UD", trig_idx)
        # The window: S or L lines go on (the event shows in the live view);
        # the EVT (and the snapshot) follow when it closes.
        yield from self._samples(rows[cfg.SIM_N_PRE:])

        if self._log:
            yield 0.0, evt
            if self._with_snapshots:       # Test mode: the record build's snapshot too
                for line in self._snapshot_lines(seq, trig_idx, rows, "ok"):
                    yield 0.0, line
            if kind == "disconnect":       # USB drops, the board resets
                yield 0.0, SimDisconnect(self._disconnect_s)
                yield from self._boot()
            return

        if kind == "device_drop":          # No buffer on the device: EVT only
            self._snap_drops += 1
            yield 0.05, evt
            return

        lines = self._snapshot_lines(seq, trig_idx, rows, kind)

        if kind == "disconnect":           # USB drops halfway through the snapshot
            for line in lines[: len(lines) // 2]:
                yield 0.0, line
            yield 0.0, SimDisconnect(self._disconnect_s)
            yield from self._boot()        # Native USB came back after a reset
            return

        evt_first = rng.random() < 0.3     # The GUI must not depend on the order
        if evt_first:
            yield 0.02, evt
        yield 0.05, lines[0]
        for line in lines[1:]:
            yield 0.0, line
        if not evt_first:
            yield 0.0, evt

    @staticmethod
    def _snapshot_lines(seq: int, trig_idx: int, rows: list, kind: str) -> list:
        """
        One snapshot as the record firmware prints it, with a real CRC-16 and
        the fault kind applied.
        @param seq       The event's seq.
        @param trig_idx  The trigger's sample count.
        @param rows      The event's (z_hp, roll, pitch) rows.
        @param kind      "ok", a snapshot fault kind, or "disconnect".
        @return The SNB, SNR and SNE lines (SNE missing for missing_sne).
        """
        n_rows = cfg.SIM_N_PRE + cfg.SIM_N_POST
        lines = ["SNB,%d,%d,%d,%d,%d,%d,%s,%s" % (seq, trig_idx, cfg.SIM_RATE_HZ,
                                                 cfg.SIM_N_PRE, cfg.SIM_N_POST, n_rows,
                                                 cfg.SIM_CHANNELS, cfg.SIM_FW)]
        crc = 0xFFFF
        for i, (z, roll, pitch) in enumerate(rows):
            if kind == "missing_row" and i == n_rows // 2:
                continue                   # A gap in i
            if kind == "wrong_length" and i >= n_rows - 24:
                break                      # Too few rows
            row = "SNR,%d,%.4f,%.4f,%.4f" % (i, z, roll, pitch)
            crc = crc16_ccitt_false(row.encode("ascii"), crc)
            lines.append(row)
        if kind == "bad_crc":
            crc ^= 0x5A5A
        if kind != "missing_sne":
            lines.append("SNE,%d,%d,%04X" % (seq, n_rows, crc))
        return lines
