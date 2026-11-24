"""
File:        train.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.2.0
Change:      0.2.0 (training-live-inference-handoff.md, request E): a new
             program. The request-3 train.py (retired 2026-11-23) is in git
             under the tag pre-manual-overhaul; none of it is reused.
Adapted from: set_determinism() and class_weights() in
             Python/codegen_task/toolkit.py of ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), MIT License, Copyright (c)
             2025 Anandghan; see LICENSE-ml-on-device-playground.txt in this
             folder. Kept: seeding Python, NumPy and TensorFlow and turning on
             TensorFlow's deterministic ops; balanced class weights (total /
             (classes x count)). Changed: tf.keras.utils.set_random_seed()
             seeds all three; no legacy-Adam swap; a class with no windows
             stops the run instead of getting the mean weight.
Description: One command from saved clips to model_data.h (request E):
             1. Picks the sessions: all (or --sessions), the newest held out
                for testing (or --test-sessions). "Newest" means its clips hold
                the latest press time (session names need not be dates). With
                one session it trains on everything and says scores are
                missing.
             2. Cuts windows with windows.py (request C, unchanged), always at
                416 Hz (P1).
             3. Stops before training if a class has no training windows.
             4. Holds out VAL_FRAC of the training clips (whole clips, per
                class, TRAIN_SEED) for early stopping. Class weights from the
                window counts. Per-channel mean and standard deviation of the
                training windows, frozen into the model (P1).
             5. Trains the fixed model (E1, P5; settings in cnn_config),
                deterministically.
             6. Tests on the held-out sessions the way the board runs
                (evaluate.py): window scores, streaming detection, false alarms
                per hour and the cutoff table. Nothing is picked (P8).
             7. Converts to float32 TFLite and runs the export checks
                (export.py). Only if all pass is model_data.h written, to the
                run folder and to firmware-bench/model_data.h, swapped in whole.
                A failed check leaves the old file untouched.
             8. Writes report.txt, windows_report.csv, model.keras,
                model.tflite and config.json to runs/<YYYYMMDD-HHMMSS>/
                (git-ignored).
             Clip files are only read. --synthetic trains on synthetic clips
             written inside the run folder, never into data/, and its
             MODEL_VERSION starts with "synthetic-".

             Command line (from this folder; README.md has both shells):
               python train.py [--sessions S ...] [--test-sessions S ...]
                               [--synthetic] [--data-dir DIR]
             Exit code: 0 model_data.h written; 1 an export check failed;
             2 stopped before training (the report says why).
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import argparse
import datetime
import json
import os
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import numpy as np

HERE = Path(__file__).resolve().parent
for _p in (HERE, HERE.parent, HERE.parent / "cnn_record_gui"):   # evaluate; cnn_config; clip_file
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cnn_config as cfg  # noqa: E402  (needs the path above)
import evaluate as ev  # noqa: E402
import export as ex  # noqa: E402
import windows as wn  # noqa: E402
from clip_file import ClipFacts, clip_header, load_clip  # noqa: E402
from take_store import TakeStore, read_header  # noqa: E402

# ============================================================================
# Constants
# ============================================================================

VERSION = "0.2.0"

NONE_LABEL = wn.NONE_LABEL
EVENT_LABELS = wn.EVENT_LABELS
MODEL_HOP = int(round(cfg.INFER_HOP_S * cfg.MODEL_RATE_HZ))   # 208

# Test hooks (TRN-4). bad-op puts a tanh layer (TFLite op TANH, not on the
# allowed list) into the model; mismatch adds MISMATCH_SHIFT to every TFLite
# probability before the Keras comparison.
FAULTS = ("bad-op", "mismatch")
MISMATCH_SHIFT = 1e-3

# Exit codes.
EXIT_WRITTEN = 0
EXIT_EXPORT_FAILED = 1
EXIT_STOPPED = 2

# cnn_config values copied into each run's config.json.
CONFIG_KEYS = ("CLASSES", "MODEL_RATE_HZ", "MODEL_WINDOW", "MODEL_CHANNELS", "MODEL_POOL_IN",
               "MODEL_CONV", "MODEL_DROPOUT", "TRAIN_LEARNING_RATE", "TRAIN_BATCH",
               "TRAIN_MAX_EPOCHS", "TRAIN_PATIENCE", "TRAIN_SEED", "VAL_FRAC", "INFER_HOP_S",
               "DET_LOCKOUT_S", "CUTOFFS", "TFLM_ALLOWED_OPS", "MAX_TFLITE_BYTES",
               "TFLITE_MATCH_TOL", "MODEL_TEST_N", "SYNTH_CLIPS_PER_CLASS",
               "SYNTH_LONG_NONE_S", "SYNTH_SEED")

# Synthetic clips: shapes and noise like the simulated device's
# (cnn_record_gui/sim_source.py, SimDevice._shape() and _sample()).
_SYN_PRESS_MS = 1_795_000_000_000   # Fixed press-time base, so files are the same every run
_SYN_DAY_MS = 86_400_000            # Sessions one day apart: the last one is the newest
_SYN_EVENT_S = 0.6                  # Event length (sim_source _EVENT_S)
_SYN_NOSE_DOWN_SIGN = -1.0          # Bench mapping: nose down is a negative pitch rate
_SYN_NOISE = (0.02, 1.5, 1.5)       # Sensor noise: z_hp (g), roll, pitch (dps)
_SYN_DELAY_S = (0.3, 3.5)           # Event start after the press
_SYN_WOBBLES_PER_6S = 1.5           # Small symmetric road wobbles on every clip
_SYN_BOOT = ("BOOT,fw=synthetic,odr=416,acc_fs=16,gyr_fs=500,hpf_hz=0.50,settle_ms=2000,"
             "bias_max=10.0,vert=az,roll=gy,pitch=gx,l_dec=4,tx_buf=32768,out_q=256,"
             "perf_ms=10000,imu_retry_ms=500,imu_good_reads=3,seg_ms=1000,cfg_ms=5000,"
             "ble=RoadQuality")

# ============================================================================
# Classes
# ============================================================================


class TrainStop(Exception):
    """The run stops before training. The message says why (exit code 2)."""


@dataclass
class RunOptions:
    """One run's choices. The defaults are what `python train.py` does."""
    sessions: Optional[list] = None        # Sessions to use (None: all, or the synthetic default)
    test_sessions: Optional[list] = None   # Held out (None: the newest, if there are 2+)
    data_dir: Path = cfg.DATA_DIR
    synthetic: bool = False
    fault: Optional[str] = None            # One of FAULTS: test hooks only
    model_data_path: Path = cfg.MODEL_DATA_PATH
    runs_dir: Path = cfg.TRAIN_RUNS_DIR
    max_epochs: int = cfg.TRAIN_MAX_EPOCHS            # Tests shorten these three
    synth_clips_per_class: int = cfg.SYNTH_CLIPS_PER_CLASS
    synth_long_s: float = cfg.SYNTH_LONG_NONE_S
    verbose: int = 2                       # Keras: one line per epoch


@dataclass
class RunResult:
    """What a run produced."""
    run_id: str
    run_dir: Path
    exit_code: int                         # EXIT_WRITTEN, EXIT_EXPORT_FAILED or EXIT_STOPPED
    model_version: str
    report_path: Path


@dataclass
class FitInfo:
    """How training went."""
    epochs_run: int
    validated: bool                        # False: no validation clips, no early stopping
    best_epoch: Optional[int] = None       # 1-based
    best_val_loss: Optional[float] = None
    best_val_acc: Optional[float] = None
    stopped_early: bool = False


class Report:
    """report.txt's body, built a section at a time."""

    def __init__(self) -> None:
        self.lines = []

    def add(self, *lines: str) -> None:
        """@param lines  Lines to append."""
        self.lines.extend(lines)

    def section(self, title: str) -> None:
        """@param title  Starts a new titled section."""
        self.lines += ["", title, "=" * len(title)]

# ============================================================================
# Functions: run folder and facts
# ============================================================================


def new_run(runs_dir: Path) -> tuple:
    """
    @param runs_dir  Folder of run folders.
    @return (run ID YYYYMMDD-HHMMSS, its new folder). A second run in the same
            second gets -2, -3, ...
    """
    base = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id, k = base, 2
    while (runs_dir / run_id).exists():
        run_id, k = "%s-%d" % (base, k), k + 1
    path = runs_dir / run_id
    path.mkdir(parents=True)
    return run_id, path


def git_commit() -> str:
    """@return The checkout's short commit, with a note if tracked files changed, or "unknown"."""
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=HERE,
                             capture_output=True, text=True, timeout=10)
        if sha.returncode != 0:
            return "unknown"
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no"],
                               cwd=HERE, capture_output=True, text=True, timeout=10)
        note = " (uncommitted changes)" if dirty.stdout.strip() else ""
        return sha.stdout.strip() + note
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _tf():
    """@return The tensorflow module, imported quietly (only training needs it)."""
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import tensorflow as tf  # pylint: disable=import-outside-toplevel
    tf.get_logger().setLevel("ERROR")
    return tf

# ============================================================================
# Functions: synthetic clips
# ============================================================================


def _syn_event(rng: np.random.Generator, label: str, rate: float) -> np.ndarray:
    """
    One event's added movement, as sim_source's _shape(): a ringing bump with
    the bench mapping's signs (pothole: drop, nose down then up; speed bump:
    the reverse).
    @param rng    Random generator (amplitude).
    @param label  "POTHOLE" or "SPEED_BUMP".
    @param rate   Sample rate.
    @return (rows, 3) z_hp, roll, pitch to add.
    """
    sign = 1.0 if label == "POTHOLE" else -1.0
    amp = rng.uniform(1.2, 3.0)
    t = np.arange(int(round(_SYN_EVENT_S * rate))) / rate
    ring = np.exp(-t / 0.12)
    z = -sign * amp * ring * np.sin(2 * np.pi * 4 * t)
    pitch = _SYN_NOSE_DOWN_SIGN * sign * 120.0 * ring * np.sin(2 * np.pi * 3 * t)
    roll = 25.0 * ring * np.sin(2 * np.pi * 5 * t)
    return np.stack([z, roll, pitch], axis=1)


def _syn_road(rng: np.random.Generator, rows: int, rate: float) -> np.ndarray:
    """
    Sensor noise plus small symmetric road wobbles (a Gaussian envelope on a
    cosine, both signs, well below an event), on every clip so they are no
    class cue.
    @param rng   Random generator.
    @param rows  Rows.
    @param rate  Sample rate.
    @return (rows, 3) z_hp, roll, pitch.
    """
    out = rng.normal(0.0, 1.0, (rows, 3)) * np.array(_SYN_NOISE)
    t = np.arange(rows) / rate
    for _ in range(int(rng.poisson(_SYN_WOBBLES_PER_6S * rows / (6.0 * rate)))):
        d = t - rng.uniform(0.0, rows / rate)
        w = rng.uniform(0.05, 0.4) * np.exp(-0.5 * (d / 0.05) ** 2) * \
            np.cos(2 * np.pi * rng.uniform(6.0, 12.0) * d)
        out[:, 0] += w
        out[:, 2] += 30.0 * w
    return out


def _syn_write(path: Path, label: str, session: str, source: str, model: np.ndarray,
               first_n: int, press_row: Optional[int], press_ms: int, rate: float,
               pre_s: float, post_s: float) -> None:
    """
    Writes one synthetic clip_v1 file with the record GUI's own header code.
    Raw columns follow sim_source: az = 1 g + z_hp, gx = pitch + 0.4,
    gy = roll - 0.3 (gyro bias in), every row ok.
    @param path       File to write.
    @param label      Its class.
    @param session    Session name.
    @param source     cfg.SOURCE_MANUAL or cfg.SOURCE_LONG.
    @param model      (rows, 3) z_hp, roll, pitch.
    @param first_n    Sample count of the first row.
    @param press_row  Row of the press (manual clips), or None.
    @param press_ms   Press (or long-recording start) time, epoch ms.
    @param rate       Sample rate.
    @param pre_s      Seconds before the press.
    @param post_s     Seconds after it (a long recording: its length).
    """
    rows = model.shape[0]
    n = np.arange(first_n, first_n + rows, dtype=np.int64)
    facts = ClipFacts()
    for v in n.tolist():
        facts.add(v, True)
    long_rec = source == cfg.SOURCE_LONG
    head = clip_header(source, label, session, press_ms,
                       None if press_row is None else int(n[press_row]), press_row, pre_s,
                       post_s, rate, facts, _SYN_BOOT, "synthetic",
                       stop_epoch_ms=press_ms + int(round(post_s * 1000)) if long_rec else None)
    body = "".join("%d,%d,0.0010,-0.0020,%.4f,%.4f,%.4f,0.2000,%.4f,%.4f,%.4f,1\n"
                   % (nv, int(round(nv * 1e6 / rate)), 1.0 + z, p + 0.4, r - 0.3, z, r, p)
                   for nv, (z, r, p) in zip(n.tolist(), model.tolist()))
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + body)


def make_synthetic(data_dir: Path, clips_per_class: int = cfg.SYNTH_CLIPS_PER_CLASS,
                   long_s: float = cfg.SYNTH_LONG_NONE_S, seed: int = cfg.SYNTH_SEED) -> list:
    """
    Writes the synthetic sessions (cfg.SYNTH_SESSIONS) into data_dir. Each has
    clips_per_class manual clips per class (1 s before, 5 s after the press,
    the event 0.3 to 3.5 s after it) and one long NONE recording of long_s;
    SYNTH_NO_NONE_SESSION has no NONE clips. Every clip has its own seeded
    generator, so the files are byte-identical every run.
    @param data_dir         Folder to create the session folders in.
    @param clips_per_class  Manual clips per class and session.
    @param long_s           Length of each long NONE recording, s (0: none).
    @param seed             Base seed.
    @return The session names.
    """
    rate = float(cfg.MODEL_RATE_HZ)
    pre = int(round(cfg.MANUAL_PRE_S * rate))
    rows = pre + int(round(cfg.MANUAL_POST_S * rate))
    press_row = pre - 1                       # The press sample is the last pre row
    for si, session in enumerate(cfg.SYNTH_SESSIONS):
        folder = Path(data_dir) / session
        folder.mkdir(parents=True, exist_ok=True)
        with_none = session != cfg.SYNTH_NO_NONE_SESSION
        for ci, label in enumerate(cfg.CLASSES):
            if label == NONE_LABEL and not with_none:
                continue
            for k in range(clips_per_class):
                rng = np.random.default_rng([seed, si, ci, k])
                model = _syn_road(rng, rows, rate)
                if label != NONE_LABEL:
                    shape = _syn_event(rng, label, rate)
                    start = press_row + int(round(rng.uniform(*_SYN_DELAY_S) * rate))
                    stop = min(rows, start + len(shape))
                    model[start:stop] += shape[:stop - start]
                _syn_write(folder / ("%s-%d.csv" % (label, k + 1)), label, session,
                           cfg.SOURCE_MANUAL, model, first_n=1_000_000 * (ci + 1) + 10_000 * k,
                           press_row=press_row,
                           press_ms=_SYN_PRESS_MS + si * _SYN_DAY_MS + (ci * 1000 + k) * 10_000,
                           rate=rate, pre_s=cfg.MANUAL_PRE_S, post_s=cfg.MANUAL_POST_S)
        if with_none and long_s > 0:
            rng = np.random.default_rng([seed, si, 99])
            model = _syn_road(rng, int(round(long_s * rate)), rate)
            _syn_write(folder / ("%s-%d.csv" % (NONE_LABEL, clips_per_class + 1)), NONE_LABEL,
                       session, cfg.SOURCE_LONG, model, first_n=50_000_000, press_row=None,
                       press_ms=_SYN_PRESS_MS + si * _SYN_DAY_MS + 50_000_000, rate=rate,
                       pre_s=0.0, post_s=long_s)
    return list(cfg.SYNTH_SESSIONS)

# ============================================================================
# Functions: sessions and splits
# ============================================================================


def session_press_ms(data_dir: Path, session: str) -> int:
    """
    @param data_dir  Data folder.
    @param session   Session name.
    @return The latest press_epoch_ms in the session's clip headers, or -1.
    """
    best = -1
    for path in sorted((Path(data_dir) / session).glob("*.csv")):
        try:
            value = read_header(path).get("press_epoch_ms", "")
        except OSError:
            continue
        if value.strip().isdigit():
            best = max(best, int(value))
    return best


def choose_sessions(available: list, sessions: Optional[list], test_sessions: Optional[list],
                    press_ms: dict) -> tuple:
    """
    @param available      Session folders that exist.
    @param sessions       Chosen sessions, or None for all.
    @param test_sessions  Sessions to hold out, or None for the newest (only
                          when two or more are chosen).
    @param press_ms       Session -> latest press time (session_press_ms()).
    @return (training sessions, held-out sessions), each sorted.
    @raise TrainStop if a session is missing or nothing is left to train on.
    """
    chosen = list(sessions) if sessions else list(available)
    named = chosen + list(test_sessions or [])
    missing = sorted(set(s for s in named if s not in available))
    if missing:
        raise TrainStop("session(s) not found: %s (found: %s)"
                        % (", ".join(missing), ", ".join(available) or "none"))
    if not chosen:
        raise TrainStop("no sessions to train on")
    if test_sessions:
        test = sorted(set(test_sessions))
        chosen = sorted(set(chosen) | set(test))
    elif len(chosen) >= 2:
        test = [max(chosen, key=lambda s: (press_ms.get(s, -1), s))]
    else:
        test = []
    train = sorted(s for s in chosen if s not in test)
    if not train:
        raise TrainStop("every chosen session is held out for testing: nothing left to "
                        "train on")
    return train, test


def validation_clips(clip_ids: np.ndarray, y: np.ndarray, frac: float, seed: int,
                     n_classes: int) -> set:
    """
    E2: whole clips for early stopping, per class, so each class with two or
    more clips gives at least one and keeps at least one for training.
    @param clip_ids   (windows,) clip of each training-session window.
    @param y          (windows,) class of each.
    @param frac       Share of each class's clips.
    @param seed       Seed (with the class index, so classes do not affect each other).
    @param n_classes  Number of classes.
    @return The validation clip IDs.
    """
    out = set()
    for k in range(n_classes):
        ids = sorted(set(clip_ids[y == k].tolist()))
        if len(ids) < 2:
            continue
        n_val = min(len(ids) - 1, max(1, int(round(frac * len(ids)))))
        pick = np.random.default_rng([seed, k]).permutation(len(ids))[:n_val]
        out.update(ids[i] for i in pick)
    return out


def class_weights(y: np.ndarray, n_classes: int) -> dict:
    """
    Balanced class weights (Jake's toolkit.class_weights()): total windows /
    (classes x the class's windows).
    @param y          (windows,) class indexes; every class present.
    @param n_classes  Number of classes.
    @return {class index: weight}.
    @raise ValueError if a class has no windows.
    """
    counts = np.bincount(y, minlength=n_classes).astype(float)
    if np.any(counts == 0):
        raise ValueError("a class has no training windows")
    w = counts.sum() / (n_classes * counts)
    return {i: float(w[i]) for i in range(n_classes)}


def channel_stats(x: np.ndarray) -> tuple:
    """
    @param x  (windows, rows, channels) training windows.
    @return (mean, std) per channel, float32; a flat channel gets std 1.
    """
    flat = x.reshape(-1, x.shape[-1]).astype(np.float64)
    std = flat.std(axis=0)
    std = np.where(std > 1e-6, std, 1.0)
    return flat.mean(axis=0).astype(np.float32), std.astype(np.float32)


def clip_counts(clip_ids: np.ndarray, y: np.ndarray, mask: np.ndarray) -> dict:
    """
    @param clip_ids  (windows,) clip IDs ("<session>/<file>").
    @param y         (windows,) classes.
    @param mask      Windows to count.
    @return {class name: clips with at least one window under mask}.
    """
    out = {c: 0 for c in cfg.CLASSES}
    seen = set()
    for cid, k in zip(clip_ids[mask].tolist(), y[mask].tolist()):
        if cid not in seen:
            seen.add(cid)
            out[cfg.CLASSES[k]] += 1
    return out


def _counts_text(counts: dict, unit: str) -> str:
    """@return "POTHOLE 3, SPEED_BUMP 2, NONE 5 <unit>"."""
    return ", ".join("%s %d" % (c, counts.get(c, 0)) for c in cfg.CLASSES) + " " + unit

# ============================================================================
# Functions: model and training
# ============================================================================


def set_determinism(seed: int) -> None:
    """
    Seeds Python, NumPy and TensorFlow and turns on TensorFlow's deterministic
    ops (Jake's toolkit.set_determinism()), so the same clips give the same
    model.
    @param seed  The seed.
    """
    tf = _tf()
    tf.keras.utils.set_random_seed(seed)
    tf.config.experimental.enable_op_determinism()


def build_model(mean: np.ndarray, std: np.ndarray, fault: Optional[str] = None):
    """
    The fixed model (E1; settings in cnn_config, P5). Scaling and
    downsampling are inside it (P1): Normalization becomes TFLite SUB and MUL,
    AveragePooling1D becomes AVERAGE_POOL_2D.
    @param mean   (3,) per-channel mean of the training windows.
    @param std    (3,) per-channel standard deviation.
    @param fault  "bad-op" adds a tanh layer (TFLite TANH, not allowed): TRN-4 only.
    @return A compiled Keras model.
    """
    tf = _tf()
    lay = tf.keras.layers
    inp = lay.Input(shape=(cfg.MODEL_WINDOW, len(cfg.MODEL_CHANNELS)), name="window")
    x = lay.Normalization(axis=-1, mean=mean, variance=np.square(std), name="scale")(inp)
    x = lay.AveragePooling1D(cfg.MODEL_POOL_IN, name="downsample")(x)
    for i, (filters, kernel, pool) in enumerate(cfg.MODEL_CONV, start=1):
        x = lay.Conv1D(filters, kernel, padding="same", activation="relu", name="conv%d" % i)(x)
        if pool:
            x = lay.MaxPooling1D(2, name="pool%d" % i)(x)
    if fault == "bad-op":
        x = lay.Activation("tanh", name="fault_tanh")(x)
    x = lay.GlobalAveragePooling1D(name="average")(x)
    x = lay.Dropout(cfg.MODEL_DROPOUT, name="dropout")(x)
    out = lay.Dense(len(cfg.CLASSES), activation="softmax", name="probs")(x)
    model = tf.keras.Model(inp, out, name="road_cnn")
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=cfg.TRAIN_LEARNING_RATE),
                  loss="sparse_categorical_crossentropy", metrics=["accuracy"])
    return model


def fit(model, x_tr: np.ndarray, y_tr: np.ndarray, x_val: np.ndarray, y_val: np.ndarray,
        weights: dict, max_epochs: int, verbose: int) -> FitInfo:
    """
    Trains with early stopping on the validation loss and the best weights
    restored. Keras 2 restores them only when it stops early, so they are set
    again here after the last epoch too.
    @param model       Compiled model.
    @param x_tr        Training windows.
    @param y_tr        Their classes.
    @param x_val       Validation windows (may be empty: then no early stopping).
    @param y_val       Their classes.
    @param weights     Class weights.
    @param max_epochs  Epoch limit.
    @param verbose     Keras verbosity.
    @return How it went.
    """
    tf = _tf()
    has_val = len(y_val) > 0
    stopper = None
    callbacks = []
    if has_val:
        stopper = tf.keras.callbacks.EarlyStopping(monitor="val_loss",
                                                   patience=cfg.TRAIN_PATIENCE,
                                                   restore_best_weights=True)
        callbacks.append(stopper)
    hist = model.fit(x_tr, y_tr, validation_data=(x_val, y_val) if has_val else None,
                     epochs=max_epochs, batch_size=cfg.TRAIN_BATCH, class_weight=weights,
                     shuffle=True, callbacks=callbacks, verbose=verbose).history
    epochs_run = len(hist["loss"])
    if not has_val:
        return FitInfo(epochs_run=epochs_run, validated=False)
    best = int(np.argmin(hist["val_loss"]))
    if stopper.best_weights is not None:
        model.set_weights(stopper.best_weights)
    return FitInfo(epochs_run=epochs_run, validated=True, best_epoch=best + 1,
                   best_val_loss=float(hist["val_loss"][best]),
                   best_val_acc=float(hist["val_accuracy"][best]),
                   stopped_early=epochs_run < max_epochs)

# ============================================================================
# Functions: the run
# ============================================================================


def _held_out_clips(data_dir: Path, ws: wn.WindowSet, test: list) -> list:
    """
    @param data_dir  Data folder.
    @param ws        The WindowSet (its report lists every file read).
    @param test      Held-out sessions.
    @return evaluate.ClipData for every readable, labeled held-out file.
    """
    out = []
    for rep in ws.report:
        if rep.session not in test or rep.label not in cfg.CLASSES or not rep.kind:
            continue
        try:
            clip = load_clip(Path(data_dir) / rep.session / rep.file)
        except (OSError, ValueError):
            continue
        out.append(ev.ClipData(clip_id="%s/%s" % (rep.session, rep.file), label=clip.label,
                               model=clip.model, n=clip.n, ok=clip.ok, rate_hz=clip.rate_hz))
    return out


def _self_test_windows(y: np.ndarray, sets: list) -> list:
    """
    MODEL_TEST_N windows for the board's boot self-test: per class, the first
    window of the first set that has one (held-out, then validation, then
    training).
    @param y     (windows,) classes.
    @param sets  [(mask, name)] in preference order.
    @return [(window index, set name)] in class order.
    """
    out = []
    for k in range(len(cfg.CLASSES)):
        for mask, name in sets:
            idx = np.flatnonzero(mask & (y == k))
            if len(idx):
                out.append((int(idx[0]), name))
                break
    return out


def _data_section(body: Report, data_dir_text: str, ws: wn.WindowSet, train: list,
                  test: list, is_tr: np.ndarray, is_val: np.ndarray,
                  is_test: np.ndarray) -> None:
    """Adds section 1 (data) to the report."""
    body.section("1. Data")
    body.add("Data folder: %s" % data_dir_text)
    for s in train + test:
        mask = ws.sessions == s
        body.add("  %-12s %-9s %s; %d windows" % (
            s, "held out" if s in test else "training",
            _counts_text(clip_counts(ws.clip_ids, ws.y, mask), "clips"), int(mask.sum())))
    for name, mask in (("Training", is_tr), ("Validation", is_val), ("Held-out test", is_test)):
        body.add("%-14s %s" % (name + ":", _counts_text(
            {c: int(np.sum(mask & (ws.y == k))) for k, c in enumerate(cfg.CLASSES)}, "windows")))
    skipped = sum(sum(r.skipped.values()) for r in ws.report)
    no_window = sum(1 for r in ws.report if r.windows_cut == 0)
    body.add("Windows skipped as damaged: %d. Files that gave no window: %d (see "
             "windows_report.csv)." % (skipped, no_window))
    body.add(*ws.warnings)


def _write_config(run_dir: Path, run_id: str, model_version: str, commit: str,
                  opts: RunOptions, train: list, test: list, val_ids: set, extra: dict) -> None:
    """Writes config.json: everything this run used."""
    conf = {
        "run_id": run_id, "model_version": model_version, "commit": commit,
        "options": {"sessions": opts.sessions, "test_sessions": opts.test_sessions,
                    "data_dir": str(opts.data_dir), "synthetic": opts.synthetic,
                    "fault": opts.fault, "model_data_path": str(opts.model_data_path),
                    "max_epochs": opts.max_epochs,
                    "synth_clips_per_class": opts.synth_clips_per_class,
                    "synth_long_s": opts.synth_long_s},
        "sessions": {"train": train, "test": test},
        "validation_clips": sorted(val_ids),
        "window_options": asdict(wn.WindowOptions(decimate=1)),
        "cnn_config": {k: getattr(cfg, k) for k in CONFIG_KEYS},
    }
    conf.update(extra)
    with open(run_dir / "config.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(conf, fh, indent=2, default=str)
        fh.write("\n")


def run_training(opts: RunOptions) -> RunResult:
    """
    One run, steps 1 to 8 of the file description.
    @param opts  The run's choices.
    @return What it produced. The exit code says whether model_data.h was written.
    """
    t0 = time.time()
    run_id, run_dir = new_run(Path(opts.runs_dir))
    model_version = (cfg.SYNTH_PREFIX if opts.synthetic else "") + run_id
    commit = git_commit()
    generated = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    notes, body = [], Report()
    exit_code, tf_version = EXIT_STOPPED, "not loaded"
    train, test, val_ids, extra = [], [], set(), {}
    try:
        # 1. Sessions.
        data_dir = Path(opts.data_dir)
        data_dir_text = str(data_dir)
        sessions = opts.sessions
        if opts.synthetic:
            data_dir = run_dir / "synthetic_data"
            data_dir_text = "synthetic_data/ in the run folder"
            print("Writing synthetic clips ...")
            make_synthetic(data_dir, opts.synth_clips_per_class, opts.synth_long_s)
            sessions = sessions or list(cfg.SYNTH_DEFAULT_SESSIONS)
            notes.append("SYNTHETIC: trained on synthetic clips (synthetic_data/ in the run "
                         "folder), not on recordings. MODEL_VERSION starts with %s."
                         % cfg.SYNTH_PREFIX)
        available = TakeStore(data_dir).sessions()
        press = {s: session_press_ms(data_dir, s) for s in available}
        train, test = choose_sessions(available, sessions, opts.test_sessions, press)
        if not test:
            notes.append("ONE SESSION: nothing is held out, so it trains on everything and the "
                         "test scores (window scores, streaming detection, false alarms per "
                         "hour) are missing.")

        # 2. Windows, always at the model's rate (P1).
        print("Cutting windows from %s ..." % ", ".join(train + test))
        ws = wn.build_windows(data_dir, train + test, wn.WindowOptions(decimate=1))
        wn.write_report(run_dir / "windows_report.csv", ws.report)
        print(wn.summary(ws))
        if len(ws.y) == 0:
            raise TrainStop("the chosen sessions gave no windows (see windows_report.csv)")
        if ws.rate_hz != cfg.MODEL_RATE_HZ or ws.X.shape[1] != cfg.MODEL_WINDOW:
            raise TrainStop("windows are %d rows at %s Hz; the model needs %d rows at %d Hz"
                            % (ws.X.shape[1], ws.rate_hz, cfg.MODEL_WINDOW, cfg.MODEL_RATE_HZ))

        # 3 and 4. Splits; every class must have training windows.
        is_test = np.isin(ws.sessions, test)
        in_train = ~is_test
        val_ids = validation_clips(ws.clip_ids[in_train], ws.y[in_train], cfg.VAL_FRAC,
                                   cfg.TRAIN_SEED, len(cfg.CLASSES))
        is_val = in_train & np.isin(ws.clip_ids, sorted(val_ids))
        is_tr = in_train & ~is_val
        _data_section(body, data_dir_text, ws, train, test, is_tr, is_val, is_test)
        empty = [c for k, c in enumerate(cfg.CLASSES) if not np.any(in_train & (ws.y == k))]
        if empty:
            raise TrainStop("no %s windows in the training sessions (%s). Every class needs "
                            "clips before training: record or relabel some, or choose other "
                            "sessions." % (" or ".join(empty), ", ".join(train)))
        if test and not np.any(is_test):
            notes.append("The held-out sessions gave no windows: test scores are missing.")
        if test and not np.any(is_test & (ws.y == cfg.CLASSES.index(NONE_LABEL))):
            notes.append("No held-out NONE clips: false alarms per hour is missing.")

        # 5. Train.
        tf = _tf()
        tf_version = tf.__version__
        set_determinism(cfg.TRAIN_SEED)
        mean, std = channel_stats(ws.X[is_tr])
        weights = class_weights(ws.y[is_tr], len(cfg.CLASSES))
        model = build_model(mean, std, opts.fault)
        if opts.fault:
            notes.append("TEST HOOK --fault %s: this run is meant to fail its export checks."
                         % opts.fault)
        print("Training on %d windows, validating on %d (up to %d epochs) ..."
              % (int(is_tr.sum()), int(is_val.sum()), opts.max_epochs))
        info = fit(model, ws.X[is_tr], ws.y[is_tr], ws.X[is_val], ws.y[is_val], weights,
                   opts.max_epochs, opts.verbose)
        model.save(run_dir / "model.keras")
        extra = {"normalization": {"channels": list(cfg.MODEL_CHANNELS),
                                   "mean": [float(v) for v in mean],
                                   "std": [float(v) for v in std]},
                 "class_weights": {cfg.CLASSES[k]: w for k, w in weights.items()},
                 "versions": {"python": platform.python_version(), "numpy": np.__version__,
                              "tensorflow": tf_version}}

        def predict(x: np.ndarray) -> np.ndarray:
            return model.predict(x, batch_size=256, verbose=0)

        body.section("2. Training")
        body.add("Model: %d parameters. Scaling (SUB, MUL), AveragePooling1D(%d), %s, "
                 "GlobalAveragePooling1D, Dropout(%.2f), Dense(%d, softmax)."
                 % (model.count_params(), cfg.MODEL_POOL_IN,
                    ", ".join("Conv1D(%d, %d)%s" % (f, k, " + MaxPooling1D(2)" if p else "")
                              for f, k, p in cfg.MODEL_CONV),
                    cfg.MODEL_DROPOUT, len(cfg.CLASSES)))
        body.add("Scaling frozen into the model: " + "; ".join(
            "%s mean %.6g, std %.6g" % (c, m, s)
            for c, m, s in zip(cfg.MODEL_CHANNELS, mean, std)))
        body.add("Class weights: " + ", ".join("%s %.3f" % (cfg.CLASSES[k], w)
                                               for k, w in weights.items()))
        body.add("Adam %.4g, batch %d, seed %d." % (cfg.TRAIN_LEARNING_RATE, cfg.TRAIN_BATCH,
                                                   cfg.TRAIN_SEED))
        if info.validated:
            body.add("Epochs run: %d of %d%s. Best epoch %d: validation loss %.4f, accuracy "
                     "%.1f%% (its weights are the model's)."
                     % (info.epochs_run, opts.max_epochs,
                        " (stopped early, patience %d)" % cfg.TRAIN_PATIENCE
                        if info.stopped_early else "",
                        info.best_epoch, info.best_val_loss, 100 * info.best_val_acc))
        else:
            body.add("No validation clips (too few clips per class): trained all %d epochs, "
                     "no early stopping." % info.epochs_run)

        # 6. Test the way the board runs.
        body.section("3. Window scores (held-out windows, top class)")
        if np.any(is_test):
            body.add(*ev.format_scores(ev.window_scores(ws.y[is_test], predict(ws.X[is_test]),
                                                        len(cfg.CLASSES)), cfg.CLASSES))
        else:
            body.add("Missing: no held-out windows.")
        body.section("4. Streaming test (held-out clips, the board's rule)")
        clips = _held_out_clips(data_dir, ws, test)
        if clips:
            scanned = ev.scan_clips(clips, predict, cfg.MODEL_WINDOW, MODEL_HOP,
                                    len(cfg.CLASSES))
            rows = ev.cutoff_table(scanned, cfg.CLASSES, NONE_LABEL, cfg.CUTOFFS,
                                   cfg.DET_LOCKOUT_S)
            events = [c for c in scanned if c.label != NONE_LABEL]
            nones = [c for c in scanned if c.label == NONE_LABEL]
            body.add("Each held-out clip is scanned as the board runs: a %.1f s window every "
                     "%.1f s (the first ends %.1f s into the clip), windows across lost "
                     "readings, a reset or ok=0 rows skipped. A detection: top class not NONE "
                     "with probability >= the cutoff; then none until %.1f s have passed."
                     % (cfg.WINDOW_S, cfg.INFER_HOP_S, cfg.WINDOW_S, cfg.DET_LOCKOUT_S))
            body.add("Event clips: %d (%s); %d had no full window and are left out."
                     % (len(events), ", ".join("%s %d" % (c, sum(1 for e in events
                                                                   if e.label == c))
                                               for c in EVENT_LABELS),
                        sum(1 for e in events if len(e.ends) == 0)))
            body.add("NONE clips: %d, %.4f h in total; %d had no full window."
                     % (len(nones), sum(c.duration_s for c in nones if len(c.ends)) / 3600.0,
                        sum(1 for c in nones if len(c.ends) == 0)))
            body.add("once: exactly one detection of the clip's class. wrong class: any "
                     "detection of the other event class. double: two or more of the "
                     "clip's class. missed: none. false alarms/h: detections in NONE clips "
                     "per hour. The table only guides: set DET_CONF in config.h by hand (P8).",
                     "")
            if not events:
                body.add("Streaming detection rate: missing (no held-out POTHOLE or "
                         "SPEED_BUMP clips).")
            body.add(*ev.format_cutoff_table(rows, EVENT_LABELS))
        else:
            body.add("Missing: no held-out clips.")

        # 7. Export.
        body.section("5. Export checks")
        fb = ex.to_float_tflite(model)
        (run_dir / "model.tflite").write_bytes(fb)
        facts = ex.tflite_facts(fb)
        for mask, name in ((is_test, "held-out"), (is_val, "validation"), (is_tr, "training")):
            if np.any(mask):
                cmp_mask, cmp_name = mask, name
                break
        x_cmp = ws.X[cmp_mask]
        tfl = ex.run_tflite(fb, x_cmp)
        if opts.fault == "mismatch":
            tfl = tfl + MISMATCH_SHIFT
        checks = ex.export_checks(facts, predict(x_cmp), tfl, cfg.MODEL_WINDOW,
                                  len(cfg.MODEL_CHANNELS), len(cfg.CLASSES),
                                  cfg.TFLM_ALLOWED_OPS, cfg.MAX_TFLITE_BYTES,
                                  cfg.TFLITE_MATCH_TOL)
        body.add("Compared on the %s windows. TFLite ran on TensorFlow's reference kernels."
                 % cmp_name)
        for c in checks:
            body.add("  %-22s %-5s %s" % (c.name, "pass" if c.ok else "FAIL", c.detail))
        body.add("Activation estimate: %d bytes for the largest op (x%.1f = %.1f KB, Jake's "
                 "nRF52840 ratio; request F prints the real arena use)."
                 % (facts.activation_bytes, ex.ARENA_OVERHEAD,
                    facts.activation_bytes * ex.ARENA_OVERHEAD / 1024.0))
        if ex.checks_passed(checks):
            picks = _self_test_windows(ws.y, [(is_test, "held-out"), (is_val, "validation"),
                                              (is_tr, "training")])
            idx = [i for i, _ in picks]
            test_x = ws.X[idx]
            info_h = ex.HeaderInfo(
                model_version=model_version, generated=generated, commit=commit,
                run_lines=_header_run_lines(ws, train, test, is_tr, is_val, is_test, opts),
                class_names=cfg.CLASSES, channels=cfg.MODEL_CHANNELS,
                window=cfg.MODEL_WINDOW, hop=MODEL_HOP, rate_hz=int(cfg.MODEL_RATE_HZ),
                ops=sorted(set(facts.ops)))
            text = ex.model_data_header(
                info_h, fb, test_x, predict(test_x),
                ["%s, %s window of %s" % (cfg.CLASSES[int(ws.y[i])], name, ws.clip_ids[i])
                 for i, name in picks])
            ex.write_atomic(run_dir / "model_data.h", text)
            ex.write_atomic(Path(opts.model_data_path), text)
            body.add("Result: all checks passed. model_data.h written to %s (and a copy in the "
                     "run folder)." % _display_path(opts.model_data_path))
            exit_code = EXIT_WRITTEN
        else:
            failed = ", ".join(c.name for c in checks if not c.ok)
            body.add("Result: FAILED (%s). No model_data.h was written; %s is unchanged."
                     % (failed, _display_path(opts.model_data_path)))
            exit_code = EXIT_EXPORT_FAILED
    except TrainStop as stop:
        body.add("", "STOPPED before training: %s" % stop)
        print("Stopped: %s" % stop)
        exit_code = EXIT_STOPPED

    # 8. Config, then the report (which lists every file).
    _write_config(run_dir, run_id, model_version, commit, opts, train, test, val_ids, extra)
    body.section("6. Files (in the run folder)")
    body.add(", ".join(sorted([p.name for p in run_dir.iterdir()] + ["report.txt"])))
    head = ["Road CNN training report (train.py %s)" % VERSION, "",
            "Run:       %s" % run_id,
            "Date:      %s" % generated,
            "Duration:  %.0f s" % (time.time() - t0),
            "Model:     %s" % model_version,
            "Commit:    %s" % commit,
            "Versions:  Python %s, NumPy %s, TensorFlow %s"
            % (platform.python_version(), np.__version__, tf_version),
            "Sessions:  training %s; held out %s" % (", ".join(train) or "-",
                                                    ", ".join(test) or "none")]
    if notes:
        head += ["", "Notes:"] + ["- " + n for n in notes]
    report_path = run_dir / "report.txt"
    with open(report_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(head + body.lines) + "\n")
    print("Report: %s" % report_path)
    return RunResult(run_id=run_id, run_dir=run_dir, exit_code=exit_code,
                     model_version=model_version, report_path=report_path)


def _display_path(path: Path) -> str:
    """@return path relative to the repository's firmware folder when inside it."""
    path = Path(path).resolve()
    firmware = HERE.parent.parent
    try:
        return str(path.relative_to(firmware.parent)).replace("\\", "/")
    except ValueError:
        return str(path)


def _header_run_lines(ws: wn.WindowSet, train: list, test: list, is_tr: np.ndarray,
                      is_val: np.ndarray, is_test: np.ndarray, opts: RunOptions) -> list:
    """@return model_data.h's comment lines about sessions and clip counts."""
    lines = []
    for s in train:
        lines.append("Training session %s: %s" % (s, _counts_text(
            clip_counts(ws.clip_ids, ws.y, ws.sessions == s), "clips")))
    lines.append("Validation (early stopping): %s of those" % _counts_text(
        clip_counts(ws.clip_ids, ws.y, is_val), "clips"))
    for s in test:
        lines.append("Held-out session %s: %s" % (s, _counts_text(
            clip_counts(ws.clip_ids, ws.y, ws.sessions == s), "clips")))
    if not test:
        lines.append("Held-out sessions: none (one session)")
    if opts.synthetic:
        lines.append("SYNTHETIC clips, not recordings")
    lines.append("Windows: training %d, validation %d, held out %d"
                 % (int(is_tr.sum()), int(is_val.sum()), int(is_test.sum())))
    return lines

# ============================================================================
# Command line
# ============================================================================


def main(argv: Optional[list] = None) -> int:
    """
    @param argv  Arguments (default: sys.argv[1:]).
    @return Exit code: EXIT_WRITTEN, EXIT_EXPORT_FAILED or EXIT_STOPPED.
    """
    ap = argparse.ArgumentParser(
        description="Train the road CNN on saved clips, test it the way the board runs it, "
                    "and write model_data.h (clip files are never changed).")
    ap.add_argument("--sessions", nargs="+", default=None,
                    help="sessions to use (default: all; with --synthetic: %s)"
                         % " ".join(cfg.SYNTH_DEFAULT_SESSIONS))
    ap.add_argument("--test-sessions", nargs="+", default=None,
                    help="sessions held out for testing (default: the newest; none if only "
                         "one session)")
    ap.add_argument("--synthetic", action="store_true",
                    help="train on synthetic clips made in the run folder "
                         "(MODEL_VERSION starts with %s)" % cfg.SYNTH_PREFIX)
    ap.add_argument("--data-dir", type=Path, default=None,
                    help="folder with one folder per session (default: firmware/cnn/data)")
    ap.add_argument("--fault", choices=FAULTS, default=None,
                    help="test hook for TRN-4 only: bad-op adds an op outside the allowed "
                         "list; mismatch shifts the TFLite outputs by %g" % MISMATCH_SHIFT)
    ap.add_argument("--model-data", type=Path, default=cfg.MODEL_DATA_PATH,
                    help="for developers: where to write model_data.h "
                         "(default: firmware-bench/model_data.h)")
    ap.add_argument("--runs-dir", type=Path, default=cfg.TRAIN_RUNS_DIR,
                    help="for developers: folder for run folders (default: runs/)")
    args = ap.parse_args(argv)
    if args.synthetic and args.data_dir is not None:
        ap.error("--synthetic makes its own clips; leave out --data-dir")
    opts = RunOptions(sessions=args.sessions, test_sessions=args.test_sessions,
                      data_dir=args.data_dir or cfg.DATA_DIR, synthetic=args.synthetic,
                      fault=args.fault, model_data_path=args.model_data,
                      runs_dir=args.runs_dir)
    result = run_training(opts)
    print({EXIT_WRITTEN: "Done: model_data.h written (%s)." % result.model_version,
           EXIT_EXPORT_FAILED: "Done, but an export check failed: model_data.h NOT written.",
           EXIT_STOPPED: "Stopped before training: see the report."}[result.exit_code])
    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
