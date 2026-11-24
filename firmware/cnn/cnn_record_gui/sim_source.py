"""
File:        sim_source.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.4.0
Change:      0.4.0 (training-live-inference-handoff.md, request G, "Simulated
             device"): firmware-bench 0.1.5-bench's live inference. BOOT and
             CFG carry the model fields and a SELFTEST line follows BOOT. Once
             SIM_WINDOW good samples (ok, no gap) have gone by, a PROB line
             follows every SIM_HOP-th sample: a high probability for an
             injected event's class while the event's middle is in the last
             SIM_WINDOW samples, otherwise for NONE. DET follows by the
             board's rule (SIM_DET_CONF, SIM_DET_LOCKOUT_MS). HLT carries the
             inference counters. New test controls: PROB lines on/off
             (set_prob) and a burst of malformed lines (inject_malformed).
             0.3.0 (manual-recording-overhaul-handoff.md, request B,
             "Simulated device"): firmware-bench 0.1.4-bench, one build. It
             streams BOOT (no thresholds), the '#' headers, L lines at 416
             per second (ok=0 for the settle time after every boot), HLT and
             SEG once a second and CFG every CFG_PERIOD_S. Nothing happens by
             itself: test controls, callable from the GUI thread, inject an
             event shape a chosen delay after a press, a gap (L lines lost, a
             jump in n), a reset (BOOT, n back to 0), ok=0 rows and a USB
             drop-out (the board then reboots). Removed with the snapshots:
             the record build, S lines, EVT lines, the snapshot faults and the
             automatic events (git tag pre-manual-overhaul).
             0.2.0: log build, CFG, bench mapping (chip Y forward).
Adapted from: the simulated-data option (generate_random_data) of
             Python/real_time_graph.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Only the idea
             is kept: instead of random 50 Hz points it emits the firmware's
             line format.
Description: A simulated device, so the GUI can be built and tested without
             hardware. stream() yields (delay, item) pairs forever: wait delay
             seconds of device time, then deliver item, a line (str, no line
             ending) or a SimDisconnect. Its fw starts with "sim-" so its clips
             cannot pass as real. Standard library only.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import math
import queue
import random
from dataclasses import dataclass
from typing import Iterator

import cnn_config as cfg

# ============================================================================
# Constants
# ============================================================================

# Gyro bias in the raw L columns (chip X, Y, Z, dps). The firmware removes it
# only from the model channels (roll, pitch).
_SIM_GYRO_BIAS = (0.4, -0.3, 0.2)

# Bench mapping (map_axes() in firmware-bench dsp.cpp): chip Y points forward,
# so pitch is chip gx and roll is chip gy, and nose down is a negative pitch
# rate: a pothole's first pitch movement is negative.
_SIM_NOSE_DOWN_SIGN = -1.0

# How long an injected event's shape lasts (s).
_EVENT_S = 0.6

# Event shapes the test control knows. NONE puts nothing in the stream.
EVENT_SHAPES = ("POTHOLE", "SPEED_BUMP", "NONE")

# Class order of the simulated model (firmware model_data.h order, O4).
_SIM_CLASSES = ("POTHOLE", "SPEED_BUMP", "NONE")

# inject_malformed() sends these: each must be counted and ignored by the GUI.
MALFORMED_LINES = ("PROB,12,0.5000,abc,0.2000,21000",   # A probability that is not a number
                   "PROB,13,1.7000,0.0000,0.0000,21000",  # Out of range
                   "PROB,14,0.5000",                     # Too few fields
                   "DET,15,,0.9000",                     # No class
                   "SELFTEST,maybe,0.1")                 # Unknown result

# ============================================================================
# Classes
# ============================================================================


@dataclass
class SimDisconnect:
    """Marker in the stream: USB is gone for duration_s, then comes back."""
    duration_s: float


class SimDevice:
    """Generates the simulated device's output, forever."""

    def __init__(self, seed: int = cfg.SIM_SEED, settle_s: float = cfg.SIM_SETTLE_S,
                 rate_hz: int = cfg.SIM_RATE_HZ) -> None:
        """
        @param seed      Random seed (same run every time).
        @param settle_s  Seconds of ok=0 L lines after each boot.
        @param rate_hz   Samples per second of device time.
        """
        self._rng = random.Random(seed)          # Event shapes
        self._noise = random.Random(seed + 1)    # Sensor noise, separate so shapes
                                                 # depend only on the seed
        self._rate = rate_hz
        self._settle_n = int(round(settle_s * rate_hz))
        self._commands: queue.Queue = queue.Queue()   # Test controls, from any thread
        self._t = 0.0               # Device time since boot, s
        self._n = 0                 # Device sample count since boot
        self._next_hlt_t = 1.0      # Device time of the next HLT and SEG, s
        self._next_cfg_t = cfg.CFG_PERIOD_S
        self._cfg_line = ""         # This boot's BOOT fields under the tag CFG
        self._pending = 0.0         # Device time not yet given out as a delay, s
        self._active = []           # [first n, rows] of injected events still running
        self._drop = 0              # L lines still to drop (a jump in n)
        self._ok0 = 0               # L lines still to send with ok=0
        self.boots = 0              # BOOT lines sent, for tests
        self.events = []            # (first n, shape) of every injected event, for tests
        self.gaps = []              # (first lost n, count) of every injected gap, for tests
        # Live inference (request G).
        self._prob_rng = random.Random(seed + 2)  # PROB values: separate, so shapes and
                                                  # noise stay as they were
        self._prob_on = True        # PROB lines on (test control)
        self._events_seen = []      # (first n, last n, shape) of events, for PROB
        self._good_run = 0          # Consecutive good samples (ok, no gap)
        self._prev_n = None
        self._hop_count = 0         # Samples since the last hop
        self._last_det_n = None     # n of the last DET
        self._inferences = 0
        self._lat_last = 0
        self._lat_max = 0
        self.probs = []             # (n, top class index) of every PROB, for tests
        self.dets = []              # (n, label) of every DET, for tests

    # ------------------------------------------------------- test controls

    def inject_event(self, shape: str, delay_s: float = cfg.SIM_INJECT_DELAY_S) -> None:
        """
        Puts an event's shape into the stream delay_s after now (a press).
        @param shape    "POTHOLE", "SPEED_BUMP" or "NONE" (nothing happens).
        @param delay_s  Seconds of device time from now to the event's start.
        """
        self._commands.put(("event", shape, float(delay_s)))

    def inject_gap(self, count: int = cfg.SIM_GAP_SAMPLES) -> None:
        """
        @param count  L lines lost on the way, starting with the next sample.
        """
        self._commands.put(("gap", int(count)))

    def inject_ok0(self, count: int = cfg.SIM_OK0_SAMPLES) -> None:
        """
        @param count  Next L lines sent with ok=0 (as after an IMU recovery).
        """
        self._commands.put(("ok0", int(count)))

    def inject_reset(self) -> None:
        """The board resets: BOOT again, n back to 0, settle again."""
        self._commands.put(("reset",))

    def inject_disconnect(self, duration_s: float = cfg.SIM_DISCONNECT_S) -> None:
        """
        @param duration_s  USB is gone this long; the board then reboots (a
                           DevKit is powered from USB).
        """
        self._commands.put(("disconnect", float(duration_s)))

    def set_prob(self, on: bool) -> None:
        """
        @param on  False stops PROB (and DET) lines while L lines go on, as if
                   inference stalled; True starts them again.
        """
        self._commands.put(("prob", bool(on)))

    def inject_malformed(self) -> None:
        """Sends one of each malformed line (MALFORMED_LINES) before the next sample."""
        self._commands.put(("malformed",))

    # -------------------------------------------------------------- stream

    def stream(self) -> Iterator[tuple]:
        """
        @return An endless iterator of (delay_s, item): wait delay_s, then
                deliver item, which is a line (str, no line ending) or a
                SimDisconnect. Test controls take effect before the next sample.
        """
        yield from self._boot()
        while True:
            yield from self._apply_commands()
            yield from self._sample()

    # ---------------------------------------------------------------- helpers

    def _apply_commands(self) -> Iterator[tuple]:
        """
        Carries out the test controls queued since the last sample.
        @return (delay, item) pairs for a reset or a disconnect.
        """
        while True:
            try:
                cmd = self._commands.get_nowait()
            except queue.Empty:
                return
            if cmd[0] == "event":
                rows = self._shape(cmd[1])
                if rows:
                    first = self._n + 1 + int(round(cmd[2] * self._rate))
                    self._active.append([first, rows])
                    self.events.append((first, cmd[1]))
                    self._events_seen.append((first, first + len(rows) - 1, cmd[1]))
            elif cmd[0] == "gap":
                self.gaps.append((self._n + 1, cmd[1]))
                self._drop += cmd[1]
            elif cmd[0] == "ok0":
                self._ok0 += cmd[1]
            elif cmd[0] == "reset":
                yield from self._boot()
            elif cmd[0] == "disconnect":
                yield 0.0, SimDisconnect(cmd[1])
                yield from self._boot()
            elif cmd[0] == "prob":
                self._prob_on = cmd[1]
            elif cmd[0] == "malformed":
                for line in MALFORMED_LINES:
                    yield 0.0, line

    def _boot(self) -> Iterator[tuple]:
        """
        A (re)boot: BOOT, SELFTEST and the column headers, as firmware-bench
        0.1.5-bench prints them; the clock, n and the settle time restart, and
        events still waiting are forgotten.
        @return (delay, line) pairs.
        """
        self._t = 0.0
        self._n = 0
        self._next_hlt_t = 1.0
        self._next_cfg_t = cfg.CFG_PERIOD_S
        self._active = []
        self._drop = 0
        self._ok0 = 0
        self._events_seen = []
        self._good_run = 0
        self._prev_n = None
        self._hop_count = 0
        self._last_det_n = None
        self._inferences = 0
        self._lat_last = 0
        self._lat_max = 0
        self.boots += 1
        boot = ("BOOT,fw=%s,odr=%d,acc_fs=16,gyr_fs=500,hpf_hz=0.50,settle_ms=%d,"
                "bias_max=10.0,vert=az,roll=gy,pitch=gx,l_dec=4,tx_buf=32768,out_q=256,"
                "perf_ms=10000,imu_retry_ms=500,imu_good_reads=3,seg_ms=1000,cfg_ms=%d,"
                "ble=RoadQuality,model=%s,classes=%s,window=%d,hop=%d,det_conf=%.2f,"
                "det_lockout_ms=%d,arena=65536,arena_used=%d,selftest=pass,"
                "selftest_diff=%.6f,inf=ok"
                % (cfg.SIM_FW, self._rate, int(round(self._settle_n * 1000.0 / self._rate)),
                   int(round(cfg.CFG_PERIOD_S * 1000)), cfg.SIM_MODEL, "|".join(_SIM_CLASSES),
                   cfg.SIM_WINDOW, cfg.SIM_HOP, cfg.SIM_DET_CONF, cfg.SIM_DET_LOCKOUT_MS,
                   cfg.SIM_ARENA_USED, cfg.SIM_SELFTEST_DIFF))
        self._cfg_line = "CFG" + boot[len("BOOT"):]
        yield 0.0, boot
        yield 0.0, "SELFTEST,pass,%.6f" % cfg.SIM_SELFTEST_DIFF
        for line in ("#L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok",
                     "#LPERF,t_ms,lines,avg_us,max_us,heap_min",
                     "#SEG,t_start,t_end,synced,n,rms_z,peak_z,roll_rms,pitch_rms",
                     "#HLT,t_ms,samples,sample_q_drops,out_q_drops,drdy_timeouts,"
                     "ble_q_drops,ble_sent,ble_fails,serial_drops,imu_recoveries,inferences,"
                     "inf_skips,lat_last_us,lat_max_us",
                     "#PROB,n,p_pothole,p_bump,p_none,lat_us", "#DET,n,class,conf",
                     "#SELFTEST,result,max_diff",
                     "#FLT,code,name", "#BLE,what,value",
                     "#CFG,<the BOOT fields again, every cfg_ms>", "BLE,advertising,0"):
            yield 0.0, line

    def _shape(self, shape: str) -> list:
        """
        One event's added movement, with the bench mapping's signs (pothole:
        nose down then up and a drop; speed bump: the reverse).
        @param shape  "POTHOLE", "SPEED_BUMP" or "NONE".
        @return (z_hp, roll, pitch) rows to add to the noise; empty for NONE.
        """
        sign = {"POTHOLE": 1.0, "SPEED_BUMP": -1.0}.get(shape)
        if sign is None:
            return []
        amp = self._rng.uniform(1.2, 3.0)
        rows = []
        for i in range(int(round(_EVENT_S * self._rate))):
            t = i / float(self._rate)
            ring = math.exp(-t / 0.12)
            z = -sign * amp * ring * math.sin(2 * math.pi * 4 * t)
            pitch = _SIM_NOSE_DOWN_SIGN * sign * 120 * ring * math.sin(2 * math.pi * 3 * t)
            roll = 25 * ring * math.sin(2 * math.pi * 5 * t)
            rows.append((z, roll, pitch))
        return rows

    def _sample(self) -> Iterator[tuple]:
        """
        Runs device time forward one sample, as the firmware does: an L line
        (unless it is being dropped), an HLT and a SEG each time device time
        passes a whole second, and a CFG every CFG_PERIOD_S.
        @return (delay, line) pairs; each delay is the device time since the
                line before.
        """
        self._n += 1
        self._t = self._n / float(self._rate)
        self._pending += 1.0 / self._rate
        nz = self._noise
        z, roll, pitch = nz.gauss(0.0, 0.02), nz.gauss(0.0, 1.5), nz.gauss(0.0, 1.5)
        still = []
        for ev in self._active:
            k = self._n - ev[0]
            if k < len(ev[1]):
                still.append(ev)
                if k >= 0:
                    dz, dr, dp = ev[1][k]
                    z, roll, pitch = z + dz, roll + dr, pitch + dp
        self._active = still
        ok = self._n > self._settle_n
        if self._ok0 > 0:
            ok = False
            self._ok0 -= 1
        if self._drop > 0:
            self._drop -= 1                  # Lost on the way: n jumps
        else:
            bx, by, bz = _SIM_GYRO_BIAS
            # Raw columns in chip order (gx = pitch, gy = roll, bias in), then
            # the model channels (bias removed), as serial_out.cpp prints them.
            yield self._pending, "L,%d,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d" % (
                self._n, int(round(self._t * 1e6)), nz.gauss(0.0, 0.01), nz.gauss(0.0, 0.01),
                1.0 + z, pitch + bx, roll + by, nz.gauss(0.0, 1.0) + bz, z, roll, pitch,
                1 if ok else 0)
            self._pending = 0.0
            yield from self._infer(ok)
        if self._t >= self._next_hlt_t:
            t_ms = int(self._next_hlt_t * 1000)
            self._next_hlt_t += 1.0
            yield self._pending, "SEG,%d,%d,0,%d,0.020,0.060,1.500,1.500" % (
                t_ms - 1000, t_ms - 1, self._rate)
            self._pending = 0.0
            yield 0.0, "HLT,%d,%d,0,0,0,0,0,0,0,0,%d,0,%d,%d" % (
                t_ms, self._n, self._inferences, self._lat_last, self._lat_max)
        if self._t >= self._next_cfg_t:
            self._next_cfg_t += cfg.CFG_PERIOD_S
            yield self._pending, self._cfg_line
            self._pending = 0.0

    def _infer(self, ok: bool) -> Iterator[tuple]:
        """
        The board's live inference, simulated, after one sent L line: counts
        consecutive good samples, and every SIM_HOP-th sample (once
        SIM_WINDOW good ones have gone by, and while PROB lines are on) gives
        a PROB line, plus DET by the board's rule.
        @param ok  The L line's ok.
        @return (delay, line) pairs: nothing, or PROB (and DET).
        """
        n = self._n
        follows = self._prev_n is not None and n == self._prev_n + 1
        self._good_run = 0 if not ok else (min(self._good_run + 1, cfg.SIM_WINDOW)
                                           if follows else 1)
        self._prev_n = n
        self._hop_count += 1
        if self._hop_count < cfg.SIM_HOP:
            return
        self._hop_count = 0
        if not self._prob_on or self._good_run < cfg.SIM_WINDOW:
            return
        oldest = n - cfg.SIM_WINDOW + 1
        self._events_seen = [e for e in self._events_seen if e[1] >= oldest]
        inside = [e for e in self._events_seen if oldest <= (e[0] + e[1]) // 2 <= n]
        rng = self._prob_rng
        top = _SIM_CLASSES.index(inside[-1][2]) if inside else len(_SIM_CLASSES) - 1
        p_top = rng.uniform(0.80, 0.97) if inside else rng.uniform(0.90, 0.99)
        rest = 1.0 - p_top
        split = rng.uniform(0.2, 0.8)
        others = [k for k in range(len(_SIM_CLASSES)) if k != top]
        probs = [0.0] * len(_SIM_CLASSES)
        probs[top] = p_top
        probs[others[0]] = rest * split
        probs[others[1]] = rest - probs[others[0]]
        lat = cfg.SIM_INFER_LAT_US + rng.randint(-500, 500)
        self._inferences += 1
        self._lat_last = lat
        self._lat_max = max(self._lat_max, lat)
        self.probs.append((n, top))
        yield 0.0, "PROB,%d,%s,%d" % (n, ",".join("%.4f" % p for p in probs), lat)
        lockout = int(round(cfg.SIM_DET_LOCKOUT_MS * self._rate / 1000.0))
        if top != len(_SIM_CLASSES) - 1 and p_top >= cfg.SIM_DET_CONF and (
                self._last_det_n is None or n - self._last_det_n >= lockout):
            self._last_det_n = n
            self.dets.append((n, _SIM_CLASSES[top]))
            yield 0.0, "DET,%d,%s,%.4f" % (n, _SIM_CLASSES[top], p_top)
