"""
File:        helpers.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.2.0
Change:      0.2.0: L lines, the log build's BOOT line, EVT with n_trig, and
             LogSample rows with bumps, for the drive log, manual Record and
             take_cut tests (requests 5 and 8).
Description: Shared test helpers. Puts the GUI folder and firmware/cnn on
             sys.path, and builds snapshot, S, L and EVT lines with the same
             format strings as firmware-bench serial_out.cpp, so the tests
             check the GUI against what the firmware actually prints.
"""

# ============================================================================
# Imports
# ============================================================================

import binascii
import math
import sys
from pathlib import Path

GUI_DIR = Path(__file__).resolve().parents[1]
CNN_DIR = GUI_DIR.parent
for p in (str(GUI_DIR), str(CNN_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

# ============================================================================
# Functions
# ============================================================================


def firmware_snapshot_lines(seq=7, trig_idx=12345, n_pre=104, n_post=520,
                            fw="0.1.2-bench", channels="az_hp/gx/gy"):
    """
    Lines exactly as the record firmware prints one snapshot:
      SNB,%u,%u,%d,%d,%d,%d,%s,%s
      SNR,%d,%.*f,%.*f,%.*f        (SNAP_DECIMALS = 4)
      SNE,%u,%d,%04X               (CRC-16/CCITT-FALSE over the SNR lines)
    @return (lines, rows): the lines without line endings, and the row values.
    """
    n_rows = n_pre + n_post
    rows = [((i % 17) * 0.0123 - 0.1, -573.4 + i * 0.5, 12.25 - i * 0.01) for i in range(n_rows)]
    lines = ["SNB,%d,%d,%d,%d,%d,%d,%s,%s" % (seq, trig_idx, 416, n_pre, n_post, n_rows,
                                             channels, fw)]
    crc = 0xFFFF
    for i, (a, b, c) in enumerate(rows):
        line = "SNR,%d,%.*f,%.*f,%.*f" % (i, 4, a, 4, b, 4, c)
        crc = binascii.crc_hqx(line.encode("ascii"), crc)
        lines.append(line)
    lines.append("SNE,%d,%d,%04X" % (seq, n_rows, crc))
    return lines, rows


def evt_line(seq=7, cls="BUMP", n_trig=None, n_peak=12400):
    """
    @param seq     Event seq.
    @param cls     Rule class.
    @param n_trig  The trigger's sample count (13th field, firmware 0.1.3-bench),
                   or None for the older 12-field line.
    @param n_peak  The peak's sample count.
    @return An EVT line in the firmware's format.
    """
    line = "EVT,%d,%d,29812345678,%s,1.234,45.0,210.5,29812,0,UD,pitch" % (seq, n_peak, cls)
    return line if n_trig is None else line + ",%d" % n_trig


def s_line(n=8, t_us=19231, values=(0.0123, -0.0040, 0.9981, 0.31, -12.5, 0.1, -0.0019)):
    """
    An S line exactly as the firmware prints it (serial_out.cpp, print_msg):
      S,%u,%lld,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%.4f
    @param n       Sample count.
    @param t_us    Device time, us.
    @param values  ax, ay, az (g), gx, gy, gz (dps), z_hp (g).
    @return The line, without a line ending.
    """
    return "S,%d,%d,%.4f,%.4f,%.4f,%.2f,%.2f,%.2f,%.4f" % ((n, t_us) + tuple(values))


# The log build's BOOT line (firmware-bench 0.1.3-bench, LOG_MODE 1).
LOG_BOOT = ("BOOT,fw=0.1.3-bench,odr=416,acc_fs=16,gyr_fs=500,thr_z=1.000,thr_roll=60.0,"
            "thr_pitch=60.0,lockout_ms=1250,rearm_frac=0.50,rearm_hold_ms=200,hpf_hz=0.50,"
            "print_every=8,settle_ms=2000,ble=RoadQuality,rec=0,log=1,l_rate=416,l_dec=4,"
            "log_tx_buf=32768,log_out_q=256,log_perf_ms=10000")


def l_line(n, z=0.0, roll=0.0, pitch=0.0, ok=1, t_us=None):
    """
    An L line exactly as the log firmware prints it (LOG_DECIMALS = 4):
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
    @return A small, varying z_hp (well under every threshold), so rows differ.
    """
    return ((n * 7) % 13 - 6) * 0.0011


def bump(n0, amp=1.2, length=60):
    """
    An event: |z_hp| = amp at n0 (the first crossing when amp >= the
    threshold), then a decaying ring.
    @param n0      Sample count of the first sample.
    @param amp     Its size, g.
    @param length  Samples it lasts.
    @return {n: (z, roll, pitch)}.
    """
    return {n0 + k: (amp * math.exp(-k / 25.0) * (1 if k % 2 == 0 else -1), 0.0, 0.0)
            for k in range(length)}


def log_samples(first_n, count, events=None, ok=1, t_jump_at=None):
    """
    LogSample rows, parsed from L lines.
    @param first_n    n of the first row.
    @param count      Number of rows.
    @param events     {n: (z, roll, pitch)} overriding the quiet rows.
    @param ok         ok of every row.
    @param t_jump_at  n at which device time jumps by one sample (a lost
                      sample without a jump in n), or None.
    @return list of LogSample.
    """
    from snapshot_protocol import parse_other_line
    events = events or {}
    out = []
    shift = 0
    for n in range(first_n, first_n + count):
        if t_jump_at is not None and n == t_jump_at:
            shift = int(round(1e6 / 416))
        z, r, p = events.get(n, (quiet_z(n), 0.0, 0.0))
        out.append(parse_other_line(l_line(n, z, r, p, ok,
                                           t_us=int(round(n * 1e6 / 416)) + shift)))
    return out
