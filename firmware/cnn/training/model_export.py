"""
File:        model_export.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Adapted from: generate_tflite() in Python/training_widget.py of
             ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             float32 TFLite conversion, and a model_data.h with a header
             guard, a "do not edit" comment, the class names in output order,
             the model as a hex byte array (12 per line) and its length.
             Changed: converted from the Keras model in memory (not from a
             SavedModel folder), written into the run folder (not into an
             Arduino sketch), the array is 16-byte aligned, and the header
             adds the input contract, the BOOT settings, the normalization
             note, the TFLite op list, the version, the sessions, the take
             counts and the commit (handoff, "Model export").
Description: Model export (cnn-record-train-handoff.md v0.4, "Model export"):
             TFLite conversion, the op list, running the TFLite model in the
             Python interpreter, the frozen test set, and the generated
             model_data.h. model_data_header() and write_frozen_test_set()
             need only numpy; the other functions import TensorFlow when
             called.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import math
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

import cnn_config as cfg

# ============================================================================
# Constants
# ============================================================================

# Bytes per line of the model array (as in the framework).
BYTES_PER_LINE = 12

# Take BOOT field -> (model_data.h macro, kind). Each macro is named after the
# firmware-bench config.h setting it must equal, with a CNN_ prefix.
BOOT_MACROS = {
    "odr": ("CNN_IMU_ODR_HZ", "int"),            # IMU_ODR_HZ
    "acc_fs": ("CNN_ACCEL_FS_G", "int"),         # ACCEL_FS_G
    "gyr_fs": ("CNN_GYRO_FS_DPS", "int"),        # GYRO_FS_DPS
    "hpf_hz": ("CNN_HPF_CUTOFF_HZ", "float"),    # HPF_CUTOFF_HZ
    "settle_ms": ("CNN_SETTLE_MS", "int"),       # SETTLE_MS
}

# ============================================================================
# Classes
# ============================================================================


@dataclass
class ModelInfo:
    """Everything model_data.h describes besides the model bytes."""
    model_version: str                 # The run name, e.g. 2026-11-22_153000
    generated: str                     # ISO 8601 time of generation
    script: str                        # e.g. "firmware/cnn/training/train.py 0.1.0"
    commit: str                        # Commit of the checkout that trained it
    class_names: tuple                 # In output order
    settings: dict                     # cfg.RUN_SETTINGS_KEYS -> value (text)
    boot_settings: dict                # cfg.BOOT_SETTINGS_KEYS -> value, or {}
    n_with_boot: int                   # Takes that carried the BOOT line
    n_takes: int                       # Takes in the run
    sessions: dict                     # "train"/"val"/"test" -> [(name, takes)]
    class_counts: dict                 # "train"/"val"/"test" -> {class: takes}
    n_train_oversampled: int           # Training takes after oversampling
    norm_mean: list                    # Per channel (inside the graph)
    norm_std: list                     # Per channel (inside the graph)
    ops: list = field(default_factory=list)   # TFLite builtin op names, sorted
    reportable: bool = True            # False if a class was below the minimum

# ============================================================================
# Functions: text helpers
# ============================================================================


def _printable(text) -> str:
    """
    @param text  Any value.
    @return Its text with every character outside printable ASCII replaced
            by "?", so header text taken from take files cannot break the C file.
    """
    return "".join(ch if 32 <= ord(ch) < 127 else "?" for ch in str(text))


def _c_string(text) -> str:
    """
    @param text  Any value.
    @return A C string literal of its printable text.
    """
    return '"%s"' % _printable(text).replace("\\", "\\\\").replace('"', '\\"')


def _comment_text(text) -> str:
    """
    @param text  Any value.
    @return Its printable text, safe inside a /* */ comment (no comment end,
            and no comment start, which -Wcomment would warn about).
    """
    return _printable(text).replace("*/", "* /").replace("/*", "/ *")


def _c_number(text, kind: str) -> Optional[str]:
    """
    @param text  A setting's value as text.
    @param kind  "int" or "float".
    @return A C literal ("2000", "0.50f"), or None if text is not a number.
    """
    try:
        if kind == "int":
            return str(int(text))
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    value = str(text).strip()
    return value + ("f" if "." in value or "e" in value.lower() else ".0f")


def _block(label: str, text: str) -> list:
    """
    @param label  Field name in the file header, e.g. "Sessions:".
    @param text   Its value, wrapped to fit.
    @return Comment lines " * Label:   text..." with continuation lines indented.
    """
    first = " * %-13s" % label
    lines = textwrap.wrap(_comment_text(text), width=78 - len(first)) or [""]
    return [first + lines[0]] + [" *" + " " * (len(first) - 2) + ln for ln in lines[1:]]


def _sessions_text(sessions: list) -> str:
    """
    @param sessions  [(name, takes)].
    @return "a (40), b (38)".
    """
    return ", ".join("%s (%d)" % pair for pair in sessions) or "none"


def _counts_text(counts: dict, class_names: tuple) -> str:
    """
    @param counts       {class: takes}.
    @param class_names  Class order.
    @return "POTHOLE 30, SPEED_BUMP 28, FALSE_TRIGGER 12".
    """
    return ", ".join("%s %d" % (c, counts.get(c, 0)) for c in class_names)

# ============================================================================
# Functions: model_data.h
# ============================================================================


def model_data_header(model: bytes, info: ModelInfo) -> str:
    """
    Generates model_data.h (never hand-edited).
    @param model  The .tflite file's bytes.
    @param info   Its description.
    @return The header file's text (LF line endings).
    """
    s = info.settings
    channels = str(s.get("channels", ""))
    units = "/".join(cfg.CHANNEL_UNITS.get(ch, "?") for ch in channels.split("/"))
    n_ch = len(channels.split("/")) if channels else 0
    split = []
    for part, name in (("train", "train"), ("val", "validation"), ("test", "test")):
        split.append("%s: %s" % (name, _sessions_text(info.sessions.get(part, []))))
    counts = []
    for part, name in (("train", "train"), ("val", "validation"), ("test", "test")):
        c = info.class_counts.get(part, {})
        counts.append("%s %d (%s)" % (name, sum(c.values()), _counts_text(c, info.class_names)))
    counts[0] += ", oversampled to %d" % info.n_train_oversampled

    out = ["/*"]
    out += _block("File:", "model_data.h")
    out += _block("Generated:", "%s by %s. DO NOT EDIT BY HAND: train again to change it."
                  % (info.generated, info.script))
    out += _block("Adapted from:", "the model_data.h writer (generate_tflite) of "
                  "Python/training_widget.py in ml-on-device-playground, "
                  "https://github.com/jake-g/ml-on-device-playground (branch codegen-classroom, "
                  "commit 47092aa), itself a fork of "
                  "https://github.com/anandghanw/ml-on-device-playground. MIT License, "
                  "Copyright (c) 2025 Anandghan.")
    out += _block("Model:", "%s, float32 TFLite, %d bytes" % (info.model_version, len(model)))
    out += _block("Commit:", info.commit)
    out += _block("Sessions:", "; ".join(split))
    out += _block("Takes:", "%d in the run; %s" % (info.n_takes, "; ".join(counts)))
    if not info.reportable:
        out += _block("WARNING:", "a class had fewer than %d takes per session type: this "
                      "model is NOT REPORTABLE (see the training report)."
                      % cfg.MIN_TAKES_PER_CLASS)
    out += _block("Description:", "The event CNN (cnn-record-train-handoff.md v0.4) and the "
                  "input contract the firmware must match. Next handoff: at boot the firmware "
                  "compares its snapshot settings with the CNN_* macros below, and a mismatch "
                  "reaches the one safe state. The arrays are static: include this file from "
                  "one .cpp file only.")
    out.append(" */")
    out.append("")
    out.append("#ifndef CNN_MODEL_DATA_H")
    out.append("#define CNN_MODEL_DATA_H")
    out.append("")

    def section(title: str) -> None:
        out.extend(["// " + "=" * 76, "// " + title, "// " + "=" * 76, ""])

    def define(name: str, value: str, note: str = "") -> None:
        line = "#define %-22s %s" % (name, value)
        if note:
            line = "%-50s// %s" % (line + " ", _printable(note))
        out.append(line.rstrip())

    section("Macros: model identity")
    define("CNN_MODEL_VERSION", _c_string(info.model_version), "Training run that made it")
    define("CNN_MODEL_LEN", str(len(model)), "Bytes in cnn_model_data")
    out.append("")

    section("Macros: input contract (each must equal the config.h setting named)")
    for key, macro, note in (("n_rows", "CNN_N_ROWS", "SNAP_ROWS: rows per input"),
                             ("n_pre", "CNN_N_PRE", "SNAP_N_PRE: row CNN_N_PRE is the trigger"),
                             ("n_post", "CNN_N_POST", "SNAP_N_POST"),
                             ("rate_hz", "CNN_RATE_HZ", "SNAP_RATE_HZ")):
        value = _c_number(s.get(key), "int")
        if value is None:
            out.append("// %s: not a whole number in the takes (%s)" % (macro,
                                                                      _printable(s.get(key))))
        else:
            define(macro, value, note)
    define("CNN_N_CH", str(n_ch), "SNAP_N_CH: channels per row")
    define("CNN_CHANNELS", _c_string(channels), "SNAP_CHANNELS, in input order")
    define("CNN_UNITS", _c_string(units), "Raw units: normalization is inside the model")
    define("CNN_FW", _c_string(s.get("fw", "")), "FW_VERSION the takes were recorded with")
    out.append("")
    out.append("// Preprocessing: firmware-bench dsp.cpp, a first-order IIR high-pass on vertical")
    out.append("// accel (hpf_step) and gyro bias removed after the settle time. The take")
    out.append("// headers do not carry these settings; CNN_FW stands in for them, and the")
    if info.boot_settings:
        out.append("// values below come from the BOOT lines kept in %d of %d takes."
                   % (info.n_with_boot, info.n_takes))
        for key, (macro, kind) in BOOT_MACROS.items():
            value = _c_number(info.boot_settings.get(key), kind)
            if value is None:
                out.append("// %s: not recorded (BOOT %s=%s)" % (
                    macro, key, _printable(info.boot_settings.get(key, "?"))))
            else:
                define(macro, value, "BOOT %s" % key)
    else:
        out.append("// BOOT line was lost in every take, so these are not defined:")
        out.append("// %s." % ", ".join(m for m, _ in BOOT_MACROS.values()))
    out.append("")
    out.append("// Normalization (C10): inside the model graph. Feed raw values. For reference")
    out.append("// only, the frozen training statistics per channel:")
    for ch, mean, std in zip(channels.split("/"), info.norm_mean, info.norm_std):
        out.append("//   %-6s mean %+.6g, std %.6g" % (_printable(ch), mean, std))
    out.append("")

    section("Macros: output")
    define("CNN_N_CLASSES", str(len(info.class_names)))
    define("CNN_N_OPS", str(len(info.ops)), "TFLite builtin ops used")
    define("CNN_OPS", _c_string(",".join(info.ops)), "For a trimmed op resolver")
    out.append("")

    section("Constants")
    out.append("// Class names in output order.")
    out.append("static const char *const CNN_CLASS_NAMES[CNN_N_CLASSES] = {%s};"
               % ", ".join(_c_string(c) for c in info.class_names))
    out.append("")
    out.append("// The .tflite model, 16-byte aligned for TFLite Micro.")
    out.append("alignas(16) static const unsigned char cnn_model_data[CNN_MODEL_LEN] = {")
    for i in range(0, len(model), BYTES_PER_LINE):
        out.append("  " + ", ".join("0x%02x" % b for b in model[i:i + BYTES_PER_LINE]) + ",")
    out.append("};")
    out.append("")
    out.append("#endif  // CNN_MODEL_DATA_H")
    return "\n".join(out) + "\n"

# ============================================================================
# Functions: frozen test set
# ============================================================================


def write_frozen_test_set(path: Path, x: np.ndarray, y_true: np.ndarray,
                          expected_probs: np.ndarray, keras_probs: np.ndarray,
                          files: list, rule_classes: list, class_names: tuple,
                          model_version: str) -> None:
    """
    Saves the frozen test set the next handoff replays on the device.
    @param path            Output .npz file.
    @param x               (n, n_rows, n_ch) float32 raw inputs (g, dps).
    @param y_true          (n,) true class index.
    @param expected_probs  (n, n_classes) outputs of the .tflite model.
    @param keras_probs     (n, n_classes) outputs of the Keras model.
    @param files           Take file per input, relative to the data folder.
    @param rule_classes    Rule class per input, as saved.
    @param class_names     Class order.
    @param model_version   The run name.
    """
    np.savez(path, x=x.astype(np.float32), y_true=y_true.astype(np.int64),
             expected_probs=expected_probs.astype(np.float32),
             keras_probs=keras_probs.astype(np.float32),
             files=np.array(files, dtype=str), rule_class=np.array(rule_classes, dtype=str),
             class_names=np.array(class_names, dtype=str),
             model_version=np.array(model_version, dtype=str))

# ============================================================================
# Functions: TensorFlow (imported when called)
# ============================================================================


def convert_to_tflite(model) -> bytes:
    """
    @param model  Trained Keras model.
    @return The float32 .tflite model (no quantization; the ESP32-S3 has an FPU).
    """
    import tensorflow as tf
    converter = tf.lite.TFLiteConverter.from_keras_model(model)
    return converter.convert()


def tflite_ops(model: bytes) -> list:
    """
    @param model  .tflite bytes.
    @return Sorted names of the TFLite ops the model uses, or [] if this
            TensorFlow cannot list them.
    """
    import tensorflow as tf
    interpreter = tf.lite.Interpreter(model_content=model)
    try:
        details = interpreter._get_ops_details()  # No public call lists the ops in TF 2.15
    except AttributeError:
        return []
    return sorted({d["op_name"] for d in details})


def run_tflite(model: bytes, x: np.ndarray) -> np.ndarray:
    """
    Runs the .tflite model one input at a time, as the device will.
    @param model  .tflite bytes.
    @param x      (n, n_rows, n_ch) float32 inputs.
    @return (n, n_classes) float32 output probabilities.
    """
    import tensorflow as tf
    interpreter = tf.lite.Interpreter(model_content=model)
    interpreter.allocate_tensors()
    inp = interpreter.get_input_details()[0]
    out = interpreter.get_output_details()[0]
    probs = []
    for sample in x:
        interpreter.set_tensor(inp["index"], sample[np.newaxis].astype(np.float32))
        interpreter.invoke()
        probs.append(interpreter.get_tensor(out["index"])[0].copy())
    if not probs:
        return np.zeros((0, int(out["shape"][-1])), dtype=np.float32)
    return np.stack(probs).astype(np.float32)
