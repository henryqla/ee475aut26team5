"""
File:        cnn_config.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.4.0
Change:      0.4.0 (manual-recording-overhaul-handoff.md, requests B and C;
             owner, 2026-11-23): manual recording only. Classes are POTHOLE,
             SPEED_BUMP and NONE (O4). New: the record keys (B2), the clip
             length around a press (B1), the clip file format, the sounds,
             the simulated device's test controls, and the window-cutting
             values of request C (W1-W4, C5). Removed with the code that used
             them: snapshot, EVT-matching, drive-log, MARK, cutter, weak/,
             background/ and manual/ settings, the 624-row take shape, and the
             request-3 training settings (that code was retired by the owner;
             git tag pre-manual-overhaul).
             0.3.2: WEAK_FLOOR_FRAC removed. 0.3.1: CFG_PERIOD_S. 0.3.0: drive
             log, manual Record, window cutting. 0.2.0: training and export.
Description: Every tunable value for the CNN record GUI and the training
             window cutter, each with its status and reason: one Python config
             location. Firmware values live in firmware/firmware-bench/config.h,
             not here; the few repeated here are what the simulated device
             sends.
"""

# ============================================================================
# Imports
# ============================================================================

from pathlib import Path

# ============================================================================
# Constants: classes and labels
# ============================================================================

# DECIDED (O4, 2026-11-23). Class index order for training (y). NONE is never
# sent to the app, so the data contract does not change. Uppercase, no
# hyphens: clip files are named <LABEL>-<n>.csv and split on the last '-'.
CLASSES = ("POTHOLE", "SPEED_BUMP", "NONE")

# Every label a clip can be saved or relabeled under, in dropdown order.
# Older takes may still carry FALSE_TRIGGER or UNLABELED; they are listed as
# they are and only the owner relabels them.
LABELS = CLASSES

# DRAFT (B2). One key both labels and records a clip.
RECORD_KEYS = {"P": "POTHOLE", "B": "SPEED_BUMP", "N": "NONE"}

# DRAFT (B2). L starts and stops a long NONE recording; Backspace moves the
# last clip saved in this run to the session's _deleted folder.
LONG_KEY = "L"
DELETE_LAST_KEY = "Backspace"

# Label of long recordings (O8: continuous normal road).
LONG_LABEL = "NONE"

# ============================================================================
# Constants: storage
# ============================================================================

# DRAFT (C13). Clips go to firmware/cnn/data/<session>/<LABEL>-<n>.csv. Found
# from this file's location, not the working directory.
DATA_DIR = Path(__file__).resolve().parent / "data"

# Subfolder of a session that a delete moves clips into, so nothing is ever
# destroyed by the GUI. The window cutter reads only <session>/*.csv.
DELETED_DIR_NAME = "_deleted"

# DRAFT. The GUI log: one file per day, firmware/cnn/logs/record_gui_<date>.log.
LOG_DIR = Path(__file__).resolve().parent / "logs"

# DECIDED (C9). The clip file layout: "# key=value" header lines, then one row
# per sample, the L line's fields without the tag, exactly as received.
CLIP_FORMAT = "clip_v1"

# The clip header's source values.
SOURCE_MANUAL = "manual"   # One P / B / N press
SOURCE_LONG = "long"       # A long NONE recording (L to start, L to stop)

# A long recording's rows go to a hidden file in the session folder while it
# runs (<session>/.long-<epoch ms>.partial), so its length is not limited by
# memory. Stopping turns it into a NONE clip. A file left by a crash is never
# deleted by the GUI.
LONG_PARTIAL_PREFIX = ".long-"
LONG_PARTIAL_SUFFIX = ".partial"

# The IMU rate when no BOOT (or CFG) line has been seen yet (firmware
# IMU_ODR_HZ). Sizes the clip buffer and the time axes.
DEFAULT_RATE_HZ = 416

# ============================================================================
# Constants: manual recording (request B)
# ============================================================================

# DRAFT (B1). A clip keeps MANUAL_PRE_S of L samples before the press (the
# press sample is the last of them) and MANUAL_POST_S after it: 416 + 2080 =
# 2496 rows at 416 Hz. Passengers press before the wheel reaches the event.
MANUAL_PRE_S = 1.0
MANUAL_POST_S = 5.0

# DRAFT (handoff, request B step 4). If the stream stops, the clip is saved
# with whatever arrived this long after its countdown ends.
MANUAL_SAVE_GRACE_S = 1.0

# ============================================================================
# Constants: sounds (request B step 3)
# ============================================================================

# DRAFT. A short tone at the press and a different two-note tone when the
# clip is saved, so the passenger never has to look.
SOUND_PRESS_HZ = 880
SOUND_PRESS_MS = 120
SOUND_SAVED_HZ = (660, 990)
SOUND_SAVED_MS = 100           # Each of the two notes
SOUND_VOLUME = 0.6             # 0..1

# ============================================================================
# Constants: serial link
# ============================================================================

# DRAFT. Matches D14 (115200). The bench build uses native USB, which ignores it.
GUI_BAUD = 115200

# DRAFT. How often the GUI retries opening a port after it disappears (a reset
# re-enumerates native USB).
RECONNECT_PERIOD_S = 1.0

# DRAFT. Serial read timeout; also how often the reader thread checks for stop.
SERIAL_READ_TIMEOUT_S = 0.1

# The firmware's CFG_PERIOD_MS: the board repeats its BOOT settings as a CFG
# line this often, because BOOT goes out once, at reset, and a PC that
# connects (or reconnects after a reset) usually misses it. The simulated
# device uses it too.
CFG_PERIOD_S = 5.0

# DRAFT (handoff). The reader thread hands L samples to the GUI thread in
# batches this often (s), not one Qt signal per sample.
SAMPLE_BATCH_S = 0.05

# ============================================================================
# Constants: live IMU view
# ============================================================================

# DRAFT. Seconds the live view shows. Display only: clips come from the L
# samples themselves, never from this view.
LIVE_WINDOW_S = 10.0

# DRAFT (handoff). The live view draws every LIVE_DECIMATE-th L sample (~52
# a second), so the plot's load stays small.
LIVE_DECIMATE = 8

# DRAFT. Two drawn points further apart than this in device time get a break
# between them (samples lost on the device or the USB link). Drawn points are
# ~19 ms apart, so this is ~10 missing points.
LIVE_GAP_S = 0.2

# DRAFT. How often the live plot is redrawn (ms), and how often its number
# readout is updated (ms; slower, so the digits can be read).
LIVE_REDRAW_MS = 50
LIVE_READOUT_MS = 250

# DRAFT. No L sample for this long (s) while connected is shown as a warning.
LIVE_STALE_S = 1.0

# ============================================================================
# Constants: simulated device
# ============================================================================

# The simulated device's firmware version. Starts with "sim-" so clips recorded
# from it can never be mistaken for real ones.
SIM_FW = "sim-0.1.4-bench"
SIM_RATE_HZ = DEFAULT_RATE_HZ

# DRAFT. Fixed seed, so a simulated run is the same every time.
SIM_SEED = 475

# After every boot the simulated device sends SIM_SETTLE_S of L lines with
# ok=0 (the firmware's SETTLE_MS: gyro bias not ready yet).
SIM_SETTLE_S = 2.0

# DRAFT (handoff test control). On a press, the simulated device puts an event
# shape into its stream this long after it (0 to 4.5 s in the GUI).
SIM_INJECT_DELAY_S = 1.5

# Test control buttons: L lines lost on the way (a jump in n), L lines sent
# with ok=0, and how long a simulated USB drop-out lasts (the board then
# reboots, as a DevKit powered from USB does).
SIM_GAP_SAMPLES = 20
SIM_OK0_SAMPLES = 50
SIM_DISCONNECT_S = 2.0

# ============================================================================
# Constants: training windows (request C)
# ============================================================================

# The training code's folder (firmware/cnn/training/), and where the window
# cutter's command line writes its report: TRAIN_RUNS_DIR/windows_<date_time>/.
# Git-ignored: keep the runs worth keeping elsewhere.
TRAINING_DIR = Path(__file__).resolve().parent / "training"
TRAIN_RUNS_DIR = TRAINING_DIR / "runs"

# DRAFT (W1). Model window, and windows cut from each POTHOLE / SPEED_BUMP
# clip, each placing the clip's peak at a random point between PEAK_LO and
# PEAK_HI of the window, using WINDOW_SEED.
WINDOW_S = 2.0
K_PER_EVENT = 5
PEAK_LO = 0.2
PEAK_HI = 0.8
WINDOW_SEED = 475

# DRAFT (handoff). NONE clips are cut every NONE_HOP_S (half a window).
NONE_HOP_S = WINDOW_S / 2

# DRAFT (handoff). The peak score is smoothed with a moving average this long.
PEAK_SMOOTH_S = 0.05

# DRAFT (C5). Decimation factor for the model input (1 = 416 Hz, none). Above
# 1, a low-pass FIR (windowed sinc, Hamming) with DECIMATE_TAPS_PER_FACTOR
# taps per unit of the factor runs first, cutting off at DECIMATE_CUTOFF_FRAC
# of the new Nyquist frequency.
DECIMATE = 1
DECIMATE_TAPS_PER_FACTOR = 16
DECIMATE_CUTOFF_FRAC = 0.8

# DRAFT (handoff, request C). Old 624-row snapshot-era takes (1.5 s at 416 Hz)
# are read only when the window is this long or shorter.
OLD_TAKE_MAX_WINDOW_S = 1.5

# DRAFT (W4). Below these, the cutter warns that held-out scores will be
# noisy; it never refuses to run.
MIN_CLIPS_PER_CLASS = 30
MIN_SESSIONS = 3
