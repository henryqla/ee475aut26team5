"""
File:        helpers.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B): the
             snapshot, EVT and S line builders are gone with those lines; BOOT
             is firmware-bench 0.1.4-bench's; log_samples() can add jumps in
             n, a reset and ok=0 rows; old_take_text() writes an older
             snapshot-era take for the "old takes stay usable" tests.
             0.2.0: L lines, the log build's BOOT line, LogSample rows.
Description: Shared test helpers. Puts the GUI folder and firmware/cnn on
             sys.path, and builds L lines with the same format string as
             firmware-bench serial_out.cpp, so the tests check the GUI against
             what the firmware actually prints.
"""

# ============================================================================
# Imports
# ============================================================================

import math
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1]
CNN_DIR = GUI_DIR.parent
for p in (str(GUI_DIR), str(CNN_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ============================================================================
# Constants
# ============================================================================

# firmware-bench 0.1.4-bench's BOOT line (serial_out.cpp, SETTINGS_FMT).
BOOT = ("BOOT,fw=0.1.4-bench,odr=416,acc_fs=16,gyr_fs=500,hpf_hz=0.50,settle_ms=2000,"
        "bias_max=10.0,vert=az,roll=gy,pitch=gx,l_dec=4,tx_buf=32768,out_q=256,perf_ms=10000,"
        "imu_retry_ms=500,imu_good_reads=3,seg_ms=1000,cfg_ms=5000,ble=RoadQuality")

# ============================================================================
# Functions
# ============================================================================


def l_line(n, z=0.0, roll=0.0, pitch=0.0, ok=1, t_us=None):
    """
    An L line exactly as the firmware prints it (L_DECIMALS = 4):
      L,%u,%lld,%.4f x 9,%d
    @param n      Sample count.
    @param z      z_hp, g.
    @param roll   Roll rate, dps (bias removed).
    @param pitch  Pitch rate, dps (bias removed).
    @param ok     1 once the gyro bias is ready.
    @param t_us   Device time, us (default: n samples at 416 Hz).
    @return The line, without a line ending.
    """
    t = int(round(n * 1e6 / 416)) if t_us is None else t_us
    return "L,%d,%d,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%.4f,%d" % (
        n, t, 0.0012, -0.0021, 1.0 + z, roll + 0.4, pitch - 0.3, 0.2, z, roll, pitch, ok)


def quiet_z(n):
    """
    @param n  Sample count.
    @return A small, varying z_hp, so rows differ.
    """
    return ((n * 7) % 13 - 6) * 0.0011


def bump(n0, amp=1.2, length=60):
    """
    An event: |z_hp| = amp at n0, then a decaying ring.
    @param n0      Sample count of the first sample.
    @param amp     Its size, g.
    @param length  Samples it lasts.
    @return {n: (z, roll, pitch)}.
    """
    return {n0 + k: (amp * math.exp(-k / 25.0) * (1 if k % 2 == 0 else -1), 0.0, 0.0)
            for k in range(length)}


def log_samples(first_n, count, events=None, ok=1, skip=None, ok0=None):
    """
    LogSample rows, parsed from L lines.
    @param first_n  n of the first row.
    @param count    Number of n values covered (rows = count minus skipped).
    @param events   {n: (z, roll, pitch)} overriding the quiet rows.
    @param ok       ok of every row (unless in ok0).
    @param skip     Set of n values that are lost (a jump in n), or None.
    @param ok0      Set of n values sent with ok=0, or None.
    @return list of LogSample.
    """
    from snapshot_protocol import parse_line
    events = events or {}
    skip = skip or set()
    ok0 = ok0 or set()
    out = []
    for n in range(first_n, first_n + count):
        if n in skip:
            continue
        z, r, p = events.get(n, (quiet_z(n), 0.0, 0.0))
        out.append(parse_line(l_line(n, z, r, p, 0 if n in ok0 else ok)))
    return out


def old_take_text(label="POTHOLE", n_pre=104, n_post=520):
    """
    An older snapshot-era take, as take_store.save_take() wrote it before
    0.1.4-bench (three columns: z_hp, roll, pitch).
    @param label   Its label.
    @param n_pre   Rows before the trigger.
    @param n_post  Rows from the trigger on.
    @return The file text.
    """
    head = ("# take_format=1\n# label=%s\n# session=old\n# fw=0.1.2-bench\n# seq=3\n"
            "# rate_hz=416\n# n_pre=%d\n# n_post=%d\n# n_rows=%d\n# channels=az_hp/gx/gy\n"
            "# rule_class=BUMP\n# columns=az_hp,gx,gy\n" % (label, n_pre, n_post, n_pre + n_post))
    rows = "".join("%.4f,%.4f,%.4f\n" % (quiet_z(i), 0.1 * (i % 5), -0.2) for i in range(n_pre + n_post))
    return head + rows
