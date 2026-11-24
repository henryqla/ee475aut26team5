"""
File:        cnn_config.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.6.0
Change:      0.6.0 (training-live-inference-handoff.md, request G): the Live
             Inference tab's settings (no-data time, smoothing G1, recent
             detections G2, the cutoff shown before the board's arrives, its
             refresh) and the simulated device's live inference (model name,
             window, hop, cutoff, lockout, timing). SIM_FW is
             sim-0.1.5-bench.
             0.5.0 (training-live-inference-handoff.md, request E; owner,
             2026-11-24): training and export settings for train.py: the
             fixed model (E1), training (E2), the board's live-inference rule
             mirrored for the streaming test (hop 0.5 s, lockout 2 s), the
             cutoff table, the export checks (E5), model_data.h and the
             synthetic clips.
             0.4.0 (manual-recording-overhaul-handoff.md, requests B and C;
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
Description: Every tunable value for the CNN record GUI, the training
             window cutter and train.py, each with its status and reason: one
             Python config location. Firmware values live in
             firmware/firmware-bench/config.h, not here; the few repeated here
             are what the simulated device sends, and the board's
             live-inference rule that train.py's streaming test copies.
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
SIM_FW = "sim-0.1.5-bench"
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

# ============================================================================
# Constants: training and export (request E)
# ============================================================================

# DECIDED (P1, owner 2026-11-24). The model takes the board's raw 2 s window
# (z_hp, roll, pitch at 416 Hz, oldest first) and does its own scaling and
# downsampling, so train.py always cuts windows with decimation 1, whatever
# DECIMATE says. These are also model_data.h's MODEL_RATE_HZ, MODEL_WINDOW and
# channel order.
MODEL_RATE_HZ = DEFAULT_RATE_HZ
MODEL_WINDOW = int(round(WINDOW_S * MODEL_RATE_HZ))   # 832
MODEL_CHANNELS = ("z_hp", "roll", "pitch")

# DRAFT (E1; P5: fixed, not tuned). The model after the per-channel scaling:
# AveragePooling1D(MODEL_POOL_IN) takes 416 Hz to 104 Hz; then one Conv1D
# (ReLU, "same" padding) per MODEL_CONV entry (filters, kernel, followed by
# MaxPooling1D(2)?); GlobalAveragePooling1D; Dropout(MODEL_DROPOUT) while
# training; Dense(3, softmax). About 6,100 parameters.
MODEL_POOL_IN = 4
MODEL_CONV = ((16, 7, True), (32, 5, True), (32, 3, False))
MODEL_DROPOUT = 0.2

# DRAFT (E1, E2). Training: Adam, batch, epochs, early stopping on the
# validation loss with the best weights restored, and the validation share
# (whole clips, per class, from the training sessions). TRAIN_SEED seeds the
# validation split, the weights and the shuffling, so two runs on the same
# clips give the same model and report.
TRAIN_LEARNING_RATE = 0.001
TRAIN_BATCH = 32
TRAIN_MAX_EPOCHS = 100
TRAIN_PATIENCE = 10
TRAIN_SEED = 475
VAL_FRAC = 0.15

# The board's live-inference rule (request F), copied here for train.py's
# streaming test. These must equal firmware-bench's values.
# DRAFT (request F): the board runs the model every INFER_HOP_S (MODEL_HOP =
# 208 samples).
INFER_HOP_S = 0.5
# DECIDED (owner 2026-11-24, F4 changed from 1 s): after a detection, the next
# one is allowed once at least DET_LOCKOUT_S has passed. Must equal config.h
# DET_LOCKOUT_MS / 1000 (2000 ms).
DET_LOCKOUT_S = 2.0

# DECIDED (P8). The report's cutoff table: streaming results at each cutoff x.
# train.py never picks one; the owner sets DET_CONF in config.h by hand.
CUTOFFS = tuple(round(0.50 + 0.05 * i, 2) for i in range(10))   # 0.50 .. 0.95

# DRAFT (E5). Export checks. TFLM_ALLOWED_OPS is the op list request D checked
# on the board (libcheck) and request F's resolver registers: an exported model
# may use only these. MAX_TFLITE_BYTES: flatbuffer limit. TFLITE_MATCH_TOL:
# largest allowed difference between a Keras and a TFLite probability.
TFLM_ALLOWED_OPS = ("CONV_2D", "MAX_POOL_2D", "AVERAGE_POOL_2D", "MEAN", "FULLY_CONNECTED",
                    "SOFTMAX", "RESHAPE", "EXPAND_DIMS", "SUB", "MUL")
MAX_TFLITE_BYTES = 256 * 1024
TFLITE_MATCH_TOL = 1e-4

# model_data.h: written here on success (generated, never hand-edited), with
# MODEL_TEST_N self-test windows (one per class) and their Keras outputs.
MODEL_DATA_PATH = Path(__file__).resolve().parent.parent / "firmware-bench" / "model_data.h"
MODEL_TEST_N = len(CLASSES)

# Synthetic clips (python train.py --synthetic): written only inside the run
# folder, never into DATA_DIR, so they never mix with real clips. Shapes like
# the simulated device's. SYNTH_NO_NONE_SESSION has event clips only (for the
# boundary tests); the default run uses SYNTH_DEFAULT_SESSIONS and holds out
# the newest. MODEL_VERSION of a synthetic model starts with SYNTH_PREFIX.
SYNTH_SESSIONS = ("syn_1", "syn_2", "syn_no_none")
SYNTH_DEFAULT_SESSIONS = ("syn_1", "syn_2")
SYNTH_NO_NONE_SESSION = "syn_no_none"
SYNTH_CLIPS_PER_CLASS = 30
SYNTH_LONG_NONE_S = 60.0
SYNTH_SEED = 475
SYNTH_PREFIX = "synthetic-"

# ============================================================================
# Constants: Live Inference tab (request G)
# ============================================================================

# The tab only reads and shows what the board's model says (PROB and DET
# lines); its controls change the display, never the board (P6).
# DRAFT (handoff). No PROB line for this long while connected shows NO DATA.
LIVE_INF_STALE_S = 2.0

# DRAFT (G1). Display smoothing: an exponential moving average, shown =
# a x shown + (1 - a) x new, with a from 0 (off, the default) to
# LIVE_INF_SMOOTH_MAX (Jake's range).
LIVE_INF_SMOOTH_DEFAULT = 0.0
LIVE_INF_SMOOTH_MAX = 0.85

# DRAFT (G2). Recent detections listed, newest first.
LIVE_INF_RECENT_N = 10

# The display cutoff before the board's det_conf (BOOT or CFG) arrives: the
# firmware's starting DET_CONF (F4).
LIVE_INF_DEFAULT_CUTOFF = 0.60

# DRAFT. How often the card's "time since the last update" and the NO DATA
# check refresh (ms).
LIVE_INF_REFRESH_MS = 250

# ============================================================================
# Constants: simulated device, live inference (request G)
# ============================================================================

# What the simulated device's BOOT says about its model, and how its PROB and
# DET lines behave: the firmware's window, hop, starting cutoff and lockout,
# so the tab behaves as it will with the board.
SIM_MODEL = "sim-model"
SIM_WINDOW = MODEL_WINDOW                                 # 832
SIM_HOP = int(round(INFER_HOP_S * MODEL_RATE_HZ))         # 208
SIM_DET_CONF = LIVE_INF_DEFAULT_CUTOFF
SIM_DET_LOCKOUT_MS = int(round(DET_LOCKOUT_S * 1000))     # 2000
SIM_INFER_LAT_US = 21000                                  # DRAFT, a made-up Invoke time
SIM_ARENA_USED = 26000                                    # DRAFT, a made-up arena use
SIM_SELFTEST_DIFF = 1e-6
