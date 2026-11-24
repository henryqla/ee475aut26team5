"""
File:        cnn_config.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.2
Change:      0.3.2 (owner, 2026-11-23; changes L6/M2): WEAK_FLOOR_FRAC
             removed and weak/ no longer written. A take of an event below
             MISS_FRAC is peak-aligned into the session folder like any
             other, however weak. WEAK_DIR_NAME stays so takes already in
             weak/ still count as cut.
             0.3.1: CFG_PERIOD_S (the firmware's CFG_PERIOD_MS).
             0.3.0: drive log, manual Record and window cutting
             (drive-log-manual-record-handoff.md v0.2, requests 5 and 6;
             one-press-manual-takes-handoff.md v0.1, request 8): the take
             shape for takes cut from L lines, the folder names, and every
             DRAFT value of both handoffs. SIM_FW is now sim-0.1.3-bench.
             0.2.0: training and export settings (request 3; C10, C11, C12
             decided by the owner 2026-11-22).
Description: Every tunable value for the CNN record GUI, the window cutter and
             the training pipeline, per cnn-record-train-handoff.md v0.4 and
             drive-log-manual-record-handoff.md v0.2, "DRAFT values": one
             Python config location, each value with its status and reason.
             Firmware values live in firmware/firmware-bench/config.h, not
             here. The few repeated here are what the simulated device sends,
             and the take shape and gap rule that takes cut on the PC must
             share with the firmware's snapshots (the log build's BOOT line
             does not carry them).
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

# DECIDED (L5, 2026-11-21). Subfolders of a session. The training loader reads
# only <session>/*.csv, so none of these is ever loaded as a take by default:
# drives/drive-<n>.log   every line of a drive (master data, never deleted)
# manual/<LABEL>-<n>.csv the raw 6 s around each Record press (M1)
# weak/<LABEL>-<n>.csv   no longer written (owner, 2026-11-23: weak events go
#                        in the session folder). Older takes found here
#                        still count as cut, so they are never cut twice.
# background/NONE-<n>.csv "nothing happening" windows cut from drive logs (L8)
DRIVES_DIR_NAME = "drives"
MANUAL_DIR_NAME = "manual"
WEAK_DIR_NAME = "weak"
BACKGROUND_DIR_NAME = "background"

# Label of background windows (handoff, request 6). Not a class and not in
# LABELS: background takes are only ever written to background/.
BACKGROUND_LABEL = "NONE"

# Versions of the drive log layout ('#' header, board lines, PC and MARK
# lines) and of the raw manual recording layout.
DRIVE_LOG_FORMAT_VERSION = 1
MANUAL_FORMAT_VERSION = 1

# The cutter's report, rewritten in the session folder on every run.
CUT_REPORT_NAME = "cut_report.txt"

# ============================================================================
# Constants: take shape (shared with the firmware's snapshots)
# ============================================================================

# DECIDED (C4). Every take is 624 rows: TAKE_N_PRE before the aligned sample
# and TAKE_N_POST from it on, the firmware's SNAP_N_PRE / SNAP_N_POST at
# 416 Hz. Takes cut from L lines (manual Record, cut_windows.py) take the
# shape from here, because the log build's BOOT line does not carry it.
TAKE_N_PRE = 104
TAKE_N_POST = 520
TAKE_N_ROWS = TAKE_N_PRE + TAKE_N_POST

# DECIDED (C3). The model channels, named as the firmware's SNAP_CHANNELS.
TAKE_CHANNELS = "az_hp/gx/gy"

# The firmware's SNAP_MAX_GAP_US (1.5 sample periods at 416 Hz): two
# neighbouring samples further apart in device time than this mean one was
# lost without n skipping (merged data-ready edges). A snapshot with such a
# gap is abandoned on the device; a window cut on the PC is skipped the same way.
MAX_SAMPLE_GAP_US = 3605

# The IMU rate when no BOOT (or CFG) line has been seen yet (firmware
# IMU_ODR_HZ). Only sizes the manual Record buffer; takes always use the rate
# from BOOT.
DEFAULT_RATE_HZ = 416

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

# The firmware's CFG_PERIOD_MS: the board repeats its BOOT settings as a CFG
# line this often, because BOOT goes out once, at reset, and a PC that
# connects (or reconnects after a reset) usually misses it. Record and Start
# log wait at most this long for the settings. The simulated device uses it too.
CFG_PERIOD_S = 5.0

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

# DRAFT (handoff, request 5). In the log build the live view draws every
# LIVE_DECIMATE-th L sample, so the plot's load stays what it is with S lines.
LIVE_DECIMATE = 8

# ============================================================================
# Constants: log build, drive log and manual Record (request 5, request 8)
# ============================================================================

# DRAFT (handoff). The reader thread hands L samples to the GUI thread in
# batches this often (s), not one Qt signal per sample.
SAMPLE_BATCH_S = 0.05

# DRAFT (handoff). A PC line (laptop time and the latest n) this often (s),
# plus one at start, stop, a lost port and a reconnect.
PC_LINE_PERIOD_S = 1.0

# DRAFT (handoff). The drive log is flushed at least this often (s), so a
# crash loses at most about this much.
LOG_FLUSH_S = 1.0

# How often the reader thread reports the drive log's status to the GUI (s),
# and checks the free disk space (s).
LOG_STATUS_PERIOD_S = 0.5
DISK_CHECK_PERIOD_S = 5.0

# DRAFT (handoff). Below this much free disk space (bytes) the log status
# warns: about 6 hours of logging at 160 MB per hour.
LOW_DISK_BYTES = 1_000_000_000

# DECIDED (L4, 2026-11-21). Manual Record keeps the last MANUAL_PRE_S of L
# samples and collects MANUAL_POST_S more after a press, so a press just
# before or just after a bump both catch it. R starts it.
MANUAL_PRE_S = 3.0
MANUAL_POST_S = 3.0
MANUAL_RECORD_KEY = "R"

# ============================================================================
# Constants: alignment and window cutting (request 6, request 8)
# ============================================================================

# DECIDED (L6, 2026-11-21; changed by the owner 2026-11-23). A MARK labels
# an EVT take if it comes within MARK_MATCH_S after the trigger. An event with
# no threshold crossing is aligned at MISS_FRAC of the BOOT thresholds; below
# that it is aligned on its peak, however small. Strength never decides
# whether or where a take is saved: every one goes in the session folder
# (there is no floor and no weak/ folder any more). Only a stretch with no
# movement at all gives nothing.
MARK_MATCH_S = 3.0
MISS_FRAC = 0.5

# DRAFT (handoff). A peak-aligned take's peak is placed the session's typical
# crossing-to-peak gap after the aligned row: the median of n_peak - trigger
# over the session's event takes, or 0 with fewer than PEAK_GAP_MIN_TAKES.
PEAK_GAP_MIN_TAKES = 10

# DECIDED (L8, 2026-11-21; the count is DRAFT). Background windows per drive
# log, the seed that picks them, and how far (s) they stay from any trigger
# or mark. 0 windows turns them off.
BACKGROUND_PER_LOG = 20
BACKGROUND_SEED = 475
BACKGROUND_CLEAR_S = 3.0

# ============================================================================
# Constants: simulated device
# ============================================================================

# The simulated device's firmware version. Starts with "sim-" so takes recorded
# from it can never be mistaken for real ones.
SIM_FW = "sim-0.1.3-bench"

# Snapshot settings the simulated device sends (the firmware's C4 values).
SIM_RATE_HZ = DEFAULT_RATE_HZ
SIM_N_PRE = TAKE_N_PRE
SIM_N_POST = TAKE_N_POST
SIM_CHANNELS = TAKE_CHANNELS

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

# Log mode of the simulated device (request 5): after every boot it sends
# SIM_SETTLE_S of L lines with ok=0 (the firmware's SETTLE_MS, gyro bias not
# ready yet); every SIM_GAP_EVERY-th event, SIM_GAP_SAMPLES L lines are lost
# in the quiet stretch before it (a jump in n). 0 = never.
SIM_SETTLE_S = 2.0
SIM_GAP_EVERY = 5
SIM_GAP_SAMPLES = 20

# ============================================================================
# Constants: training and export (request 3)
# ============================================================================

# The training code's folder (firmware/cnn/training/).
TRAINING_DIR = Path(__file__).resolve().parent / "training"

# Owner (2026-11-22). Each run saves its log, model, model_data.h, report and
# frozen test set in TRAIN_RUNS_DIR/<YYYY-MM-DD_HHMMSS>/. Git-ignored: keep the
# runs worth keeping elsewhere.
TRAIN_RUNS_DIR = TRAINING_DIR / "runs"

# Where make_sample_set.py writes the made-up take sets for TRN-1, TRN-3 and
# TRN-4. Rebuilt from a fixed seed on demand, so it is git-ignored.
SAMPLE_DATA_DIR = TRAINING_DIR / "sample_data"

# DRAFT (handoff). Fixed seed: two runs on the same data give the same numbers
# (TRN-1). Seeds the session choice, the oversampling and TensorFlow.
TRAIN_SEED = 475

# DRAFT (handoff, "DRAFT values": "ordinary small-CNN defaults").
TRAIN_BATCH_SIZE = 16
TRAIN_MAX_EPOCHS = 100
TRAIN_PATIENCE = 10            # Early stopping: epochs without a lower validation loss
TRAIN_LEARNING_RATE = 0.001    # Adam's default

# DECIDED (C11, owner 2026-11-22): split by whole sessions. One session in
# SPLIT_HOLDOUT_EVERY (at least one) is held out as the test set, then one in
# SPLIT_HOLDOUT_EVERY of the rest (at least one) as the validation set for
# early stopping; the rest train. Below 10 sessions that is "leave one session
# out". The sessions are picked with TRAIN_SEED unless the run names them.
SPLIT_HOLDOUT_EVERY = 5

# A run needs at least one session each to train, validate and test.
SPLIT_MIN_SESSIONS = 3

# DECIDED (C11): takes per class per session type before a model is reported.
# A run below it still trains and exports, but its report says NOT REPORTABLE.
MIN_TAKES_PER_CLASS = 30

# Session types, read from the session name: a "_"-separated word, as in
# 2026-11-12_bench or 2026-11-14_car_route1. Anything else is "other". The
# minimum above is counted per type.
SESSION_TYPES = ("bench", "car")

# DRAFT (handoff, "Base model", from the framework). Kernel and pooling get
# tuned once there is data. At 416 Hz a kernel of 3 spans about 7 ms.
CNN_CONV_FILTERS = (32, 32)    # One Conv1D + MaxPooling1D pair per entry
CNN_KERNEL_SIZE = 3
CNN_POOL_SIZE = 2
CNN_DENSE_UNITS = 16

# Unit of each model channel (C3: accel in g, gyro in dps), for model_data.h.
CHANNEL_UNITS = {"az_hp": "g", "gx": "dps", "gy": "dps"}

# Take header fields that every take in one run must share; the loader refuses
# a mixed set and names the files (C7, handoff "Loader checks"). fw stands in
# for the preprocessing settings, which the SNB line does not carry: any change
# to the HPF, full scales or axis mapping must also bump FW_VERSION.
RUN_SETTINGS_KEYS = ("rate_hz", "n_pre", "n_post", "n_rows", "channels", "fw")

# BOOT fields compared as well, among the takes whose header holds the BOOT
# line (a native USB reset often loses it).
BOOT_SETTINGS_KEYS = ("odr", "acc_fs", "gyr_fs", "hpf_hz", "settle_ms")

# Rule class as saved in the take header (Serial EVT names) -> training class.
# Only the training code maps BUMP to SPEED_BUMP (handoff). UNKNOWN and
# "missing" have no entry: owner (2026-11-22), they count as wrong for every
# class. The rule never answers FALSE_TRIGGER.
RULE_TO_CLASS = {"POTHOLE": "POTHOLE", "BUMP": "SPEED_BUMP"}

# Owner sets (TRN-2): the largest allowed difference between the Keras and the
# TFLite output probabilities on the frozen test set. None = not set yet: the
# report shows the difference without judging it.
TFLITE_MATCH_TOL = None
