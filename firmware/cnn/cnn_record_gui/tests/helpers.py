"""
File:        helpers.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Description: Shared test helpers. Puts the GUI folder and firmware/cnn on
             sys.path, and builds snapshot lines with the same format strings
             as firmware-bench serial_out.cpp (send_snapshot), so the tests
             check the GUI against what the firmware actually prints.
"""

# ============================================================================
# Imports
# ============================================================================

import binascii
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


def evt_line(seq=7, cls="BUMP"):
    """
    @return An EVT line in the firmware's format.
    """
    return "EVT,%d,12400,29812345678,%s,1.234,45.0,210.5,29812,0,UD,pitch" % (seq, cls)


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
