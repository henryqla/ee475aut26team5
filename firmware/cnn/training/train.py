"""
File:        train.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Adapted from: the training flow of TrainingWorker.run() in
             Python/training_widget.py of ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: Keras
             fit of the CNN, then a confusion matrix and accuracy on held-out
             takes. Changed (handoff, "Training pipeline changes"): split by
             session before oversampling, oversampling of the training takes
             only, early stopping on validation sessions, fixed batch size and
             epoch limit, a fixed seed with deterministic ops, the rule
             baseline, per-class recall, export into a run folder, and a
             command line; it is a plain module, not a Qt worker.
Description: Training and export pipeline for the event CNN
             (cnn-record-train-handoff.md v0.4, request 3). One run: load and
             check the takes, split by session (C11), oversample the training
             takes, train with normalization inside the graph (C10), score
             the held-out sessions against the rule baseline, then export
             model.keras, model.tflite, model_data.h, the frozen test set and
             the report into firmware/cnn/training/runs/<YYYY-MM-DD_HHMMSS>/.
             Every run writes train.log there, failed runs too.

             Command line (from this folder, with the shared venv, in
             Command Prompt):
               "%USERPROFILE%\\venvs\\ee475-cnn\\Scripts\\python.exe" train.py
               ... train.py --data-dir sample_data\\trn1 --allow-sim
               ... train.py --sessions A B C D --test-sessions D --val-sessions C
             The record GUI's Training tab calls run_training() the same way.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
CNN_DIR = HERE.parent
for _p in (HERE, CNN_DIR, CNN_DIR / "cnn_record_gui"):  # This folder, cnn_config, take_store
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402  (needs the path above)

import cnn_config as cfg  # noqa: E402
from dataset_split import SplitError, oversample, split_sessions, take_indices  # noqa: E402
from eval_metrics import (NO_ANSWER, accuracy, confusion_matrix, fmt_share,  # noqa: E402
                          matrix_markdown, matrix_text, per_class_recall, rule_predictions)
from model_export import ModelInfo, model_data_header, write_frozen_test_set  # noqa: E402
from take_loader import (LoadReport, MixedSettingsError, load_dataset,  # noqa: E402
                         session_type, thin_classes)
from take_store import checkout_commit  # noqa: E402

# ============================================================================
# Constants
# ============================================================================

VERSION = "0.1.0"
SCRIPT = "firmware/cnn/training/train.py %s" % VERSION

# Logger every run writes to; train.log, the command line and the GUI attach to it.
LOGGER_NAME = "training"

# Split parts, in report order, with their names.
PARTS = (("train", "train"), ("val", "validation"), ("test", "test"))

# ============================================================================
# Classes
# ============================================================================


class TrainingError(RuntimeError):
    """A run that cannot go on; the message says why, for the user."""
    run_dir: Optional[Path] = None   # Set by run_training: where train.log is


@dataclass
class RunOptions:
    """What one run uses. Defaults come from cnn_config."""
    data_dir: Path = cfg.DATA_DIR
    sessions: Optional[list] = None        # None = every session folder
    test_sessions: Optional[list] = None   # None = chosen with the seed
    val_sessions: Optional[list] = None    # None = chosen with the seed
    allow_sim: bool = False                # Accept simulated takes (sample sets)
    out_root: Path = cfg.TRAIN_RUNS_DIR
    seed: int = cfg.TRAIN_SEED
    max_epochs: int = cfg.TRAIN_MAX_EPOCHS

# ============================================================================
# Functions: helpers
# ============================================================================


def _new_run_dir(out_root: Path) -> Path:
    """
    @param out_root  Folder that holds the runs.
    @return A new, empty folder named after the current time.
    """
    out_root.mkdir(parents=True, exist_ok=True)
    base = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    path, k = out_root / base, 2
    while True:
        try:
            path.mkdir()
            return path
        except FileExistsError:   # Two runs in the same second
            path, k = out_root / ("%s_%d" % (base, k)), k + 1


def _import_tensorflow(log: logging.Logger):
    """
    Imports TensorFlow with repeatable settings.
    @param log  Run logger.
    @return The tensorflow module.
    @raise TrainingError if it is not installed.
    """
    # Before the first import only: hide start-up notices, and turn off
    # oneDNN's reordered float sums so two runs give the same numbers (TRN-1).
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
    try:
        import tensorflow as tf
    except ImportError as exc:
        if exc.name == "tensorflow":   # The package itself is missing
            raise TrainingError("TensorFlow is not installed for %s. With the owner's OK, "
                                "install it: firmware/cnn/training/README.md, Part 1."
                                % sys.executable) from exc
        # The package is there but a part of it is not: an install that
        # stopped halfway, usually on a path too long for Windows.
        raise TrainingError("TensorFlow could not be imported: %s. The install is incomplete "
                            "for %s; see firmware/cnn/training/README.md, \"If something goes "
                            "wrong\"." % (exc, sys.executable)) from exc
    if not tf.__version__.startswith("2.15."):
        log.warning("TensorFlow %s: the handoff pins 2.15 (Keras 2); results and export may "
                    "differ", tf.__version__)
    return tf


def _epoch_logger(tf, log: logging.Logger, max_epochs: int,
                  should_stop: Optional[Callable[[], bool]]):
    """
    @param tf           The tensorflow module.
    @param log          Run logger.
    @param max_epochs   Epoch limit, for the log lines.
    @param should_stop  Returns True when the user asked to stop, or None.
    @return A Keras callback that logs each epoch and stops on request; its
            stopped_by_user attribute says whether it did.
    """
    class EpochLog(tf.keras.callbacks.Callback):
        """Logs one line per epoch; stops training when asked."""
        stopped_by_user = False

        def on_epoch_end(self, epoch, logs=None):
            """
            @param epoch  Epoch index from 0.
            @param logs   Keras metrics of the epoch.
            """
            logs = logs or {}
            log.info("Epoch %3d/%d: loss %.4f, accuracy %.3f, val_loss %.4f, val_accuracy %.3f",
                     epoch + 1, max_epochs, logs.get("loss", float("nan")),
                     logs.get("accuracy", float("nan")), logs.get("val_loss", float("nan")),
                     logs.get("val_accuracy", float("nan")))
            if should_stop is not None and should_stop():
                self.model.stop_training = True
                EpochLog.stopped_by_user = True

    return EpochLog()


def _log_report(log: logging.Logger, report: LoadReport) -> None:
    """
    @param log     Run logger.
    @param report  What the loader accepted, skipped and rejected.
    """
    for level, text in report.summary_lines():
        getattr(log, level)(text)


def _counts(labels: np.ndarray, idx: np.ndarray) -> dict:
    """
    @param labels  Class index of every take.
    @param idx     Indices of some takes.
    @return {class name: takes} over cfg.CLASSES.
    """
    sub = labels[idx]
    return {c: int(np.sum(sub == k)) for k, c in enumerate(cfg.CLASSES)}


def _fingerprint(metrics: dict, probs: np.ndarray) -> str:
    """
    @param metrics  The run's metrics (no run name, times or paths in it).
    @param probs    Keras output probabilities on the test takes.
    @return 16 hex digits that match between two runs only if every metric
            and every test output is identical (TRN-1).
    """
    digest = hashlib.sha256(json.dumps(metrics, sort_keys=True).encode("utf-8"))
    digest.update(np.ascontiguousarray(probs, dtype=np.float32).tobytes())
    return digest.hexdigest()[:16]

# ============================================================================
# Functions: report
# ============================================================================


def _report_markdown(about: dict, m: dict, report: LoadReport, session_part: dict) -> str:
    """
    @param about         Run facts that are not metrics (time, commit, paths).
    @param m             The run's metrics.
    @param report        The loader's report.
    @param session_part  Session name -> "train" / "validation" / "test".
    @return report.md text.
    """
    classes = m["classes"]
    out = ["# Training report: %s" % about["run"], ""]
    if m["reportable"]:
        out.append("**Result: REPORTABLE.** Every class has at least %d takes per session type."
                   % cfg.MIN_TAKES_PER_CLASS)
    else:
        thin = "; ".join("%s %s: %d" % (t["class"], t["session_type"], t["takes"])
                         for t in m["thin_classes"])
        out.append("**Result: NOT REPORTABLE.** Fewer than %d takes per class per session type "
                   "(%s). The model was still trained and exported, so the pipeline can be "
                   "checked, but its numbers are too noisy to compare." %
                   (cfg.MIN_TAKES_PER_CLASS, thin))
    out += ["", "| | |", "| --- | --- |"]
    for key, name in (("started", "Started"), ("seconds", "Run time (s)"), ("script", "Script"),
                      ("commit", "Commit"), ("tensorflow", "TensorFlow"),
                      ("python", "Python"), ("data_dir", "Data folder"),
                      ("allow_sim", "Simulated takes"), ("seed", "Seed")):
        out.append("| %s | %s |" % (name, about[key]))
    out.append("| Metrics fingerprint | `%s` (TRN-1: two runs with the same seed must match) |"
               % about["fingerprint"])

    out += ["", "## Data", ""]
    out.append("Settings shared by every take: " + ", ".join(
        "%s=%s" % kv for kv in report.settings.items()) + ".")
    if report.boot_settings:
        out.append("BOOT settings (from %d of %d takes): %s." % (
            report.n_with_boot, len(report.takes),
            ", ".join("%s=%s" % kv for kv in report.boot_settings.items())))
    else:
        out.append("No take kept its BOOT line: the preprocessing settings are known only "
                   "through fw.")
    out += ["", "| Session | Type | Split | " + " | ".join(classes) + " | Total |",
            "| --- | --- | --- |" + " ---: |" * (len(classes) + 1)]
    for s in sorted(m["per_session"]):
        row = m["per_session"][s]
        out.append("| %s | %s | %s | %s | %d |" % (
            s, session_type(s), session_part[s], " | ".join(str(row[c]) for c in classes),
            sum(row.values())))
    out.append("")
    out.append("Skipped UNLABELED takes: %d." % len(report.unlabeled))
    for title, items in (("Skipped files", report.skipped), ("Rejected takes", report.rejected)):
        out.append("")
        out.append("%s: %d." % (title, len(items)))
        for path, why in items:
            out.append("- `%s/%s`: %s" % (path.parent.name, path.name, why))
    out.append("")
    out.append("Minimum takes per class per session type: %d. Below it: %s." % (
        cfg.MIN_TAKES_PER_CLASS, "; ".join(
            "%s %s %d" % (t["class"], t["session_type"], t["takes"])
            for t in m["thin_classes"]) or "none"))

    out += ["", "## Training", ""]
    out.append("Model: Normalization (inside the graph, C10), %s, Flatten, Dense %d ReLU, "
               "Dense %d softmax; %d parameters." % (
                   ", ".join("Conv1D %d k%d ReLU + MaxPool %d" % (f, cfg.CNN_KERNEL_SIZE,
                                                                 cfg.CNN_POOL_SIZE)
                             for f in cfg.CNN_CONV_FILTERS),
                   cfg.CNN_DENSE_UNITS, len(classes), m["params"]))
    out += ["", "| Channel | Mean | Std |", "| --- | ---: | ---: |"]
    for ch, mean, std in zip(report.settings.get("channels", "").split("/"),
                             m["normalization"]["mean"], m["normalization"]["std"]):
        out.append("| %s | %.6g | %.6g |" % (ch, mean, std))
    out.append("")
    out.append("Split (%s): train %s; validation %s; test %s." % (
        m["split"]["chosen_by"], ", ".join(m["split"]["train"]), ", ".join(m["split"]["val"]),
        ", ".join(m["split"]["test"])))
    out.append("Training takes oversampled from %d to %d (%s)." % (
        sum(m["counts"]["train"].values()), m["n_train_oversampled"],
        ", ".join("%s %d" % kv for kv in m["counts_train_oversampled"].items())))
    h, b = m["history"], m["best_epoch"] - 1
    out.append("Adam (learning rate %g), batch %d, up to %d epochs, early stopping patience %d "
               "on validation loss. Ran %d epochs; best epoch %d: loss %.4f, accuracy %.3f, "
               "validation loss %.4f, validation accuracy %.3f." % (
                   cfg.TRAIN_LEARNING_RATE, cfg.TRAIN_BATCH_SIZE, m["max_epochs"],
                   cfg.TRAIN_PATIENCE, m["epochs_run"], m["best_epoch"], h["loss"][b],
                   h["accuracy"][b], h["val_loss"][b], h["val_accuracy"][b]))

    t = m["test"]
    out += ["", "## Held-out test: %s" % ", ".join(m["split"]["test"]), ""]
    out += ["### CNN", "", matrix_markdown(np.array(t["cnn"]["confusion"]), classes, classes),
            "", "Accuracy: %s." % fmt_share(t["cnn"]["accuracy"])]
    out += ["", "### Rule baseline", "",
            matrix_markdown(np.array(t["rule"]["confusion"]), classes, t["rule"]["columns"]),
            "", "Accuracy: %s. The rule class saved in each take's header, BUMP read as "
            "SPEED_BUMP. UNKNOWN (%d) and missing (%d) count as wrong for every class; the "
            "rule never answers FALSE_TRIGGER, so its FALSE_TRIGGER recall is 0." % (
                fmt_share(t["rule"]["accuracy"]), t["rule"]["unknown"], t["rule"]["missing"])]
    out += ["", "### Per-class recall", "", "| Class | Test takes | CNN | Rule |",
            "| --- | ---: | ---: | ---: |"]
    for c in classes:
        out.append("| %s | %d | %s | %s |" % (c, m["counts"]["test"][c],
                                             fmt_share(t["cnn"]["recall"][c]),
                                             fmt_share(t["rule"]["recall"][c])))

    e = m["export"]
    out += ["", "## Export", ""]
    for name in ("model_data.h", "model.tflite", "model.keras", "frozen_test_set.npz",
                 "report.json", "train.log"):
        out.append("- `%s`" % name)
    out.append("")
    out.append("float32 TFLite, %d bytes. Ops: %s." % (e["tflite_bytes"],
                                                       ", ".join(e["ops"]) or "not listed"))
    out.append("Keras vs TFLite on the frozen test set (%d takes): largest difference in an "
               "output probability %.3g; %s." % (sum(m["counts"]["test"].values()),
                                                 e["max_abs_diff"], e["match"]))
    return "\n".join(out) + "\n"

# ============================================================================
# Functions: the run
# ============================================================================


def _run(opts: RunOptions, run_dir: Path, log: logging.Logger,
         should_stop: Optional[Callable[[], bool]]) -> None:
    """
    One training run (see the file description).
    @param opts         What to use.
    @param run_dir      The run's new folder.
    @param log          Run logger (already writing train.log).
    @param should_stop  Returns True when the user asked to stop, or None.
    @raise TrainingError when the run cannot go on.
    """
    t0 = time.monotonic()
    started = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    commit = checkout_commit(HERE)
    data_dir = Path(opts.data_dir)
    log.info("Training run %s (%s), commit %s", run_dir.name, SCRIPT, commit)
    log.info("Data folder %s; simulated takes %s", data_dir,
             "allowed" if opts.allow_sim else "refused")
    log.info("Seed %d, batch %d, up to %d epochs, early stopping patience %d", opts.seed,
             cfg.TRAIN_BATCH_SIZE, opts.max_epochs, cfg.TRAIN_PATIENCE)

    # ---- Load and check the takes (handoff, "Loader checks")
    try:
        report = load_dataset(data_dir, opts.sessions, opts.allow_sim)
    except MixedSettingsError as exc:
        _log_report(log, exc.report)   # The per-file problems first, then the refusal
        raise TrainingError(str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise TrainingError(str(exc)) from exc
    _log_report(log, report)
    takes = report.takes
    if not takes:
        raise TrainingError("No usable takes in %s" % data_dir)
    thin = thin_classes(takes)
    for kind, cls, n in thin:
        log.warning("Thin class: %d %s take(s) in %s sessions, fewer than %d: the model is "
                    "NOT REPORTABLE", n, cls, kind, cfg.MIN_TAKES_PER_CLASS)

    # ---- Split by session (C11), then oversample the training takes only
    try:
        split = split_sessions(sorted({t.session for t in takes}), opts.seed,
                               opts.test_sessions, opts.val_sessions)
    except SplitError as exc:
        raise TrainingError(str(exc)) from exc
    classes = cfg.CLASSES
    n_classes = len(classes)
    take_sessions = [t.session for t in takes]
    labels = np.array([classes.index(t.label) for t in takes], dtype=np.int64)
    x_all = np.stack([t.x for t in takes]).astype(np.float32)
    parts = {"train": take_indices(take_sessions, split.train),
             "val": take_indices(take_sessions, split.val),
             "test": take_indices(take_sessions, split.test)}
    log.info("Split by session (%s):", split.chosen_by)
    for part, name in PARTS:
        log.info("  %-10s %d takes (%s) from %s", name, len(parts[part]), ", ".join(
            "%s %d" % kv for kv in _counts(labels, parts[part]).items()),
            ", ".join(getattr(split, part)))
    for k, c in enumerate(classes):
        if not np.any(labels[parts["train"]] == k):
            log.warning("No %s take in the training sessions: the model cannot learn it", c)
    rng = np.random.default_rng(opts.seed)
    train_os = oversample(parts["train"], labels, n_classes, rng)
    log.info("Oversampled the training takes from %d to %d (%s)", len(parts["train"]),
             len(train_os), ", ".join("%s %d" % kv for kv in _counts(labels, train_os).items()))

    # ---- Train, normalization inside the graph (C10)
    tf = _import_tensorflow(log)
    log.info("TensorFlow %s", tf.__version__)
    tf.keras.backend.clear_session()
    tf.keras.utils.set_random_seed(opts.seed)        # Python, numpy and TensorFlow
    tf.config.experimental.enable_op_determinism()   # Same seed, same numbers (TRN-1)
    from cnn_model import build_model, normalization_stats
    mean, var = normalization_stats(x_all[parts["train"]])   # Originals only, not the copies
    model = build_model(x_all.shape[1:], n_classes, mean, var)
    model.summary(print_fn=lambda line, *args, **kwargs: log.info(line))
    stopper = tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=cfg.TRAIN_PATIENCE,
                                               restore_best_weights=True)
    epoch_log = _epoch_logger(tf, log, opts.max_epochs, should_stop)
    history = model.fit(x_all[train_os], labels[train_os],
                        validation_data=(x_all[parts["val"]], labels[parts["val"]]),
                        epochs=opts.max_epochs, batch_size=cfg.TRAIN_BATCH_SIZE, shuffle=True,
                        verbose=0, callbacks=[stopper, epoch_log])
    if epoch_log.stopped_by_user:
        raise TrainingError("Stopped by the user; nothing was exported")
    if stopper.stopped_epoch == 0 and stopper.best_weights is not None:
        model.set_weights(stopper.best_weights)   # Keras restores it only when it stops early
    hist = {k: [float(v) for v in vals] for k, vals in history.history.items()}
    best = int(np.argmin(hist["val_loss"]))
    log.info("Ran %d epoch(s); best epoch %d (val_loss %.4f, val_accuracy %.3f), weights from it",
             len(hist["loss"]), best + 1, hist["val_loss"][best], hist["val_accuracy"][best])

    # ---- Score the held-out test sessions, CNN and rule baseline
    test = parts["test"]
    x_test, y_test = x_all[test], labels[test]
    keras_probs = model.predict(x_test, batch_size=cfg.TRAIN_BATCH_SIZE,
                                verbose=0).astype(np.float32)
    cnn_cm = confusion_matrix(y_test, np.argmax(keras_probs, axis=1), n_classes)
    rule_names = [takes[i].rule_class for i in test]
    rule_cm = confusion_matrix(y_test, rule_predictions(rule_names, classes), n_classes,
                               n_classes + 1)
    cnn_recall, rule_recall = per_class_recall(cnn_cm), per_class_recall(rule_cm)
    log.info("Test (%s), CNN, accuracy %s:", ", ".join(split.test), fmt_share(accuracy(cnn_cm)))
    for line in matrix_text(cnn_cm, classes, classes):
        log.info("  %s", line)
    log.info("Test, rule baseline, accuracy %s:", fmt_share(accuracy(rule_cm)))
    for line in matrix_text(rule_cm, classes, list(classes) + [NO_ANSWER]):
        log.info("  %s", line)
    for k, c in enumerate(classes):
        log.info("  Recall %-13s CNN %s, rule %s", c, fmt_share(cnn_recall[k]),
                 fmt_share(rule_recall[k]))

    # ---- Export (handoff, "Model export")
    from model_export import convert_to_tflite, run_tflite, tflite_ops
    model.save(str(run_dir / "model.keras"))   # Keras 2 native format
    tflite = convert_to_tflite(model)
    (run_dir / "model.tflite").write_bytes(tflite)
    ops = tflite_ops(tflite)
    tflite_probs = run_tflite(tflite, x_test)
    max_diff = float(np.max(np.abs(tflite_probs - keras_probs))) if len(test) else 0.0
    tol = cfg.TFLITE_MATCH_TOL
    if tol is None:
        match = "tolerance not set yet (owner sets TFLITE_MATCH_TOL, TRN-2)"
    else:
        match = "%s the tolerance %g" % ("within" if max_diff <= tol else "OVER", tol)
    log.info("TFLite: %d bytes, ops %s; Keras vs TFLite largest difference %.3g, %s",
             len(tflite), ", ".join(ops) or "not listed", max_diff, match)
    write_frozen_test_set(run_dir / "frozen_test_set.npz", x_test, y_test, tflite_probs,
                          keras_probs, ["%s/%s" % (takes[i].session, takes[i].path.name)
                                        for i in test],
                          rule_names, classes, run_dir.name)

    counts = {part: _counts(labels, parts[part]) for part, _ in PARTS}
    metrics = {
        "reportable": not thin,
        "classes": list(classes),
        "settings": report.settings,
        "boot_settings": report.boot_settings,
        "thin_classes": [{"session_type": k, "class": c, "takes": n} for k, c, n in thin],
        "per_session": {s: {c: int(sum(1 for t in takes if t.session == s and t.label == c))
                            for c in classes} for s in sorted(set(take_sessions))},
        "split": {"train": split.train, "val": split.val, "test": split.test,
                  "chosen_by": split.chosen_by},
        "counts": counts,
        "n_train_oversampled": int(len(train_os)),
        "counts_train_oversampled": _counts(labels, train_os),
        "params": int(model.count_params()),
        "normalization": {"in_graph": True, "mean": [float(v) for v in mean],
                          "std": [float(v) for v in np.sqrt(var)]},
        "max_epochs": opts.max_epochs,
        "epochs_run": len(hist["loss"]),
        "best_epoch": best + 1,
        "history": hist,
        "test": {
            "cnn": {"confusion": cnn_cm.tolist(), "accuracy": accuracy(cnn_cm),
                    "recall": dict(zip(classes, cnn_recall))},
            "rule": {"confusion": rule_cm.tolist(), "columns": list(classes) + [NO_ANSWER],
                     "accuracy": accuracy(rule_cm), "recall": dict(zip(classes, rule_recall)),
                     "unknown": sum(1 for r in rule_names if r == "UNKNOWN"),
                     "missing": sum(1 for r in rule_names
                                    if r not in cfg.RULE_TO_CLASS and r != "UNKNOWN")},
        },
        "export": {"tflite_bytes": len(tflite), "ops": ops, "max_abs_diff": max_diff,
                   "tolerance": tol, "match": match},
    }
    fingerprint = _fingerprint(metrics, keras_probs)
    about = {"run": run_dir.name, "started": started, "seconds": round(time.monotonic() - t0, 1),
             "script": SCRIPT, "commit": commit, "tensorflow": tf.__version__,
             "python": sys.version.split()[0], "data_dir": str(data_dir),
             "allow_sim": "allowed" if opts.allow_sim else "refused", "seed": opts.seed,
             "fingerprint": fingerprint}

    info = ModelInfo(
        model_version=run_dir.name, generated=started, script=SCRIPT, commit=commit,
        class_names=classes, settings=report.settings, boot_settings=report.boot_settings,
        n_with_boot=report.n_with_boot, n_takes=len(takes),
        sessions={part: [(s, int(sum(1 for t in take_sessions if t == s)))
                         for s in getattr(split, part)] for part, _ in PARTS},
        class_counts=counts, n_train_oversampled=int(len(train_os)),
        norm_mean=metrics["normalization"]["mean"], norm_std=metrics["normalization"]["std"],
        ops=ops, reportable=not thin)
    (run_dir / "model_data.h").write_text(model_data_header(tflite, info), encoding="utf-8",
                                          newline="\n")
    session_part = {s: name for part, name in PARTS for s in getattr(split, part)}
    (run_dir / "report.md").write_text(_report_markdown(about, metrics, report, session_part),
                                       encoding="utf-8", newline="\n")
    (run_dir / "report.json").write_text(json.dumps({"about": about, "metrics": metrics},
                                                    indent=2), encoding="utf-8", newline="\n")
    if thin:
        log.warning("Result: NOT REPORTABLE (a class below %d takes per session type)",
                    cfg.MIN_TAKES_PER_CLASS)
    else:
        log.info("Result: REPORTABLE")
    log.info("Metrics fingerprint %s (TRN-1: two runs with the same seed must match)",
             fingerprint)
    log.info("Done in %.1f s. Run saved in %s", time.monotonic() - t0, run_dir)


def run_training(opts: RunOptions,
                 should_stop: Optional[Callable[[], bool]] = None) -> Path:
    """
    Runs one training and export into a new run folder.
    @param opts         What to use.
    @param should_stop  Checked after every epoch; returns True to stop.
    @return The run folder.
    @raise TrainingError when the run cannot go on (its run_dir attribute
           says where train.log is); other exceptions are bugs, also logged.
    """
    log = logging.getLogger(LOGGER_NAME)
    log.setLevel(logging.INFO)
    run_dir = _new_run_dir(Path(opts.out_root))
    file_handler = logging.FileHandler(run_dir / "train.log", encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(file_handler)
    try:
        _run(opts, run_dir, log, should_stop)
        return run_dir
    except TrainingError as exc:
        exc.run_dir = run_dir
        log.error("Run stopped: %s", exc)
        log.error("Log saved in %s", run_dir / "train.log")
        raise
    except Exception:
        log.exception("Unexpected error; the run stopped. Log saved in %s",
                      run_dir / "train.log")
        raise
    finally:
        log.removeHandler(file_handler)
        file_handler.close()

# ============================================================================
# Command line
# ============================================================================


def main(argv: Optional[list] = None) -> int:
    """
    @param argv  Arguments (default sys.argv[1:]).
    @return Exit code: 0 done, 1 the run stopped (see the log).
    """
    parser = argparse.ArgumentParser(
        description="Train and export the event CNN (cnn-record-train-handoff.md v0.4, "
                    "request 3). Writes a run folder under %s." % cfg.TRAIN_RUNS_DIR)
    parser.add_argument("--data-dir", type=Path, default=cfg.DATA_DIR,
                        help="folder with one subfolder per session (default %(default)s)")
    parser.add_argument("--sessions", nargs="+", metavar="SESSION",
                        help="sessions to use (default: every session folder)")
    parser.add_argument("--test-sessions", nargs="+", metavar="SESSION",
                        help="sessions to hold out for the test (default: chosen with the seed)")
    parser.add_argument("--val-sessions", nargs="+", metavar="SESSION",
                        help="sessions for early stopping (default: chosen with the seed)")
    parser.add_argument("--allow-sim", action="store_true",
                        help="accept takes from the simulated device (the sample sets)")
    parser.add_argument("--out-dir", type=Path, default=cfg.TRAIN_RUNS_DIR,
                        help="where run folders go (default %(default)s)")
    args = parser.parse_args(argv)

    log = logging.getLogger(LOGGER_NAME)
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(levelname)-7s %(message)s"))
    log.addHandler(console)
    try:
        run_training(RunOptions(data_dir=args.data_dir, sessions=args.sessions,
                                test_sessions=args.test_sessions, val_sessions=args.val_sessions,
                                allow_sim=args.allow_sim, out_root=args.out_dir))
    except TrainingError:
        return 1   # Already logged, with the reason
    finally:
        log.removeHandler(console)
    return 0


if __name__ == "__main__":
    sys.exit(main())
