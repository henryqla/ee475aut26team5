"""
File:        cnn_config.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Description: Every tunable value for the CNN record GUI (and, from request 3,
             the training pipeline), per cnn-record-train-handoff.md v0.4,
             "DRAFT values": one Python config location, each value with its
             status and reason. Firmware values live in
             firmware/firmware-bench/config.h, not here; the few repeated here
             are only what the simulated device sends, and real takes carry
             their own settings in every SNB line.
"""

# ============================================================================
# Imports
# ============================================================================

from pathlib import Path

# ============================================================================
# Constants: classes and labels
# ============================================================================

# DECIDED (C2, 2026-11-16). FALSE_TRIGGER is never sent off the device, so the
# data contract does not change. Uppercase, no hyphens: the framework split
# filenames on '-', and take files are named <LABEL>-<n>.csv.
CLASSES = ("POTHOLE", "SPEED_BUMP", "FALSE_TRIGGER")

# DRAFT (car workflow). Label of takes saved with "keep armed" before anyone
# labels them. The training loader skips these and reports how many.
UNLABELED = "UNLABELED"

# Every label the GUI offers, in dropdown order.
LABELS = CLASSES + (UNLABELED,)

# DRAFT (car workflow). One key labels the most recent take.
QUICK_LABEL_KEYS = {"P": "POTHOLE", "B": "SPEED_BUMP", "F": "FALSE_TRIGGER"}

# ============================================================================
# Constants: storage
# ============================================================================

# DRAFT (C13). Takes go to firmware/cnn/data/<session>/<LABEL>-<n>.csv. Found
# from this file's location, not the working directory.
DATA_DIR = Path(__file__).resolve().parent / "data"

# Subfolder of a session that "Delete" moves takes into, so a mis-click in a
# moving car never destroys a take. The loader reads only <session>/*.csv.
DELETED_DIR_NAME = "_deleted"

# Version of the take file layout (the "# key=value" header and data rows).
TAKE_FORMAT_VERSION = 1

# DRAFT. The GUI log (evidence for GUI-3 / GUI-4): one file per day,
# firmware/cnn/logs/record_gui_<YYYY-MM-DD>.log.
LOG_DIR = Path(__file__).resolve().parent / "logs"

# ============================================================================
# Constants: serial link
# ============================================================================

# DRAFT. Matches D14 (115200). The bench build uses native USB, which ignores it.
GUI_BAUD = 115200

# DRAFT (handoff, "Matching in the GUI"). A snapshot whose EVT has not arrived
# after this long is saved with the rule fields marked missing; an EVT whose
# snapshot has not arrived is counted as "EVT without snapshot".
EVT_MATCH_TIMEOUT_S = 5.0

# DRAFT. A snapshot with no new line for this long is rejected as missing its
# SNE. Longer than the firmware's SNAP_SEND_TIMEOUT_MS (2 s per line), so a
# slow but still-sending device is never cut off.
SNAP_STALL_TIMEOUT_S = 3.0

# DRAFT. Largest n_rows the GUI accepts in an SNB line. Real snapshots have 624;
# this only stops a corrupted SNB from making the GUI collect rows forever.
SNAP_MAX_ROWS = 20000

# DRAFT. How often the GUI retries opening a port after it disappears (a reset
# re-enumerates native USB).
RECONNECT_PERIOD_S = 1.0

# DRAFT. Serial read timeout; also how often the reader thread checks for stop.
SERIAL_READ_TIMEOUT_S = 0.1

# ============================================================================
# Constants: live IMU view
# ============================================================================

# DRAFT. Seconds of S lines the live view shows. Display only: takes always
# come from snapshots, never from this view.
LIVE_WINDOW_S = 10.0

# DRAFT. Two S lines further apart than this in device time are drawn with a
# break between them, not a straight line (lines dropped on the device or
# the USB link). The firmware sends one every PRINT_EVERY_N = 8 samples at
# 416 Hz, ~19 ms apart, so this is ~10 missing lines.
LIVE_GAP_S = 0.2

# DRAFT. How often the live plot is redrawn (ms), and how often its number
# readout is updated (ms; slower, so the digits can be read).
LIVE_REDRAW_MS = 50
LIVE_READOUT_MS = 250

# DRAFT. No S line for this long (s) while connected is shown as a warning.
LIVE_STALE_S = 1.0

# ============================================================================
# Constants: simulated device
# ============================================================================

# The simulated device's firmware version. Starts with "sim-" so takes recorded
# from it can never be mistaken for real ones.
SIM_FW = "sim-0.1.2-bench"

# Snapshot settings the simulated device sends (the firmware's C4 values).
SIM_RATE_HZ = 416
SIM_N_PRE = 104
SIM_N_POST = 520
SIM_CHANNELS = "az_hp/gx/gy"

# The simulated device sends an S line every SIM_PRINT_EVERY samples, like the
# firmware's PRINT_EVERY_N, so the live view has something to show.
SIM_PRINT_EVERY = 8

# DRAFT. Seconds between simulated events (never less than one snapshot,
# 1.5 s); every SIM_BAD_EVERY-th event is faulty (missing row, bad CRC, wrong
# length, missing SNE, or snapshot dropped on the device), cycling through the
# kinds. 0 = never.
SIM_EVENT_PERIOD_S = 3.0
SIM_BAD_EVERY = 4

# DRAFT. Every SIM_RESET_EVERY-th event the simulated device resets (BOOT
# again, seq back to 0); every SIM_DISCONNECT_EVERY-th event USB drops out for
# SIM_DISCONNECT_S, mid-snapshot. 0 = never.
SIM_RESET_EVERY = 9
SIM_DISCONNECT_EVERY = 13
SIM_DISCONNECT_S = 2.0

# DRAFT (handoff). Fixed seed, so a simulated run is the same every time.
SIM_SEED = 475
