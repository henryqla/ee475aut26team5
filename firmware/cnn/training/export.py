"""
File:        export.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-24
Version:     0.1.0
Change:      0.1.0 (training-live-inference-handoff.md, request E): new file.
Adapted from: ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), MIT License, Copyright (c)
             2025 Anandghan; see LICENSE-ml-on-device-playground.txt in this
             folder:
             - to_float_tflite() in Python/codegen_task/toolkit.py: the plain
               float32 conversion;
             - analyze_tflite() and run_tflite() in Python/codegen_task/
               deploy.py: reading the op list and running the model with the
               default delegate (XNNPACK) turned off, so the real ops show,
               and the activation-memory estimate (largest live tensors of one
               op, times the 2.2 his board measured);
             - check_contract() in toolkit.py and deploy_validator() in
               deploy.py: the input, output, op-list and size checks;
             - write_model_header() in deploy.py: model_data.h's layout (a
               do-not-edit comment, the class names in output order, the model
               as a 16-byte aligned array of 12 bytes per line, its length).
             Changed: TFLite runs on TensorFlow's reference kernels (closest
             to TFLite Micro) for the Keras comparison, which must match within
             TFLITE_MATCH_TOL with the same top class instead of 95% agreement;
             the limits come from cnn_config; checks return a list of results
             instead of raising; model_data.h gains an include guard, the run
             facts, the window contract, the self-test windows with their
             Keras outputs, and is written atomically.
Description: Model export for train.py (request E, "Export checks" and
             "Outputs"). Needs TensorFlow only in to_float_tflite(),
             tflite_facts() and run_tflite(); the checks and the header writer
             are plain Python and NumPy.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# ============================================================================
# Constants
# ============================================================================

# Bytes per line of the model array (as in the framework).
BYTES_PER_LINE = 12

# Jake's measured ratio of real arena to the activation estimate (nRF52840).
# Only a hint for the ESP32: request F prints the arena actually used.
ARENA_OVERHEAD = 2.2

# Check names, in report order.
CHECK_INPUT = "input shape"
CHECK_OUTPUT = "output shape"
CHECK_OPS = "allowed ops"
CHECK_SIZE = "size"
CHECK_MATCH = "TFLite matches Keras"

# ============================================================================
# Classes
# ============================================================================


@dataclass
class TfliteFacts:
    """What a flatbuffer holds."""
    size: int                 # Bytes
    input_shape: tuple
    input_dtype: str          # e.g. "float32"
    output_shape: tuple
    output_dtype: str
    ops: list                 # Op names in graph order (no delegate)
    activation_bytes: int     # Largest live tensors of one op, bytes


@dataclass
class Check:
    """One export check."""
    name: str
    ok: bool
    detail: str


@dataclass
class HeaderInfo:
    """The facts model_data.h's comment and macros carry."""
    model_version: str        # MODEL_VERSION: the run ID (synthetic- first for synthetic)
    generated: str            # Local time of generation, ISO 8601
    commit: str               # Commit of the checkout that trained it
    run_lines: list           # Lines about sessions and clip counts, without "// "
    class_names: tuple        # In output order
    channels: tuple           # Channel order of the input
    window: int               # MODEL_WINDOW
    hop: int                  # MODEL_HOP
    rate_hz: int              # MODEL_RATE_HZ
    ops: list                 # Distinct ops, for the comment

# ============================================================================
# Functions: TFLite
# ============================================================================


def to_float_tflite(model) -> bytes:
    """
    Plain float32 conversion (P2: no quantization).
    @param model  A Keras model.
    @return The TFLite flatbuffer.
    """
    import tensorflow as tf  # pylint: disable=import-outside-toplevel
    return tf.lite.TFLiteConverter.from_keras_model(model).convert()


def _interpreter(model_bytes: bytes, reference: bool):
    """
    @param model_bytes  A TFLite flatbuffer.
    @param reference    True: TensorFlow's reference kernels; False: its
                        optimized kernels. Never the XNNPACK delegate, which
                        hides the real ops behind one DELEGATE node.
    @return An allocated tf.lite.Interpreter.
    """
    import tensorflow as tf  # pylint: disable=import-outside-toplevel
    kind = tf.lite.experimental.OpResolverType
    interp = tf.lite.Interpreter(
        model_content=model_bytes, num_threads=1,
        experimental_op_resolver_type=(kind.BUILTIN_REF if reference
                                       else kind.BUILTIN_WITHOUT_DEFAULT_DELEGATES))
    interp.allocate_tensors()
    return interp


def tflite_facts(model_bytes: bytes) -> TfliteFacts:
    """
    @param model_bytes  A TFLite flatbuffer.
    @return Its size, input and output, ops and activation estimate.
    """
    interp = _interpreter(model_bytes, reference=False)
    ops = interp._get_ops_details()  # pylint: disable=protected-access
    tensors = {d["index"]: d for d in interp.get_tensor_details()}
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    activations = {d["index"] for d in interp.get_input_details()}
    for op in ops:
        activations.update(int(i) for i in op["outputs"])
    peak = 0
    for op in ops:
        live = [tensors[int(i)] for i in list(op["inputs"]) + list(op["outputs"])
                if int(i) in tensors and int(i) in activations]
        peak = max(peak, sum(int(np.prod(d["shape"])) * np.dtype(d["dtype"]).itemsize
                             for d in live))
    return TfliteFacts(size=len(model_bytes),
                       input_shape=tuple(int(v) for v in inp["shape"]),
                       input_dtype=np.dtype(inp["dtype"]).name,
                       output_shape=tuple(int(v) for v in out["shape"]),
                       output_dtype=np.dtype(out["dtype"]).name,
                       ops=[op["op_name"] for op in ops],
                       activation_bytes=int(peak))


def run_tflite(model_bytes: bytes, x: np.ndarray) -> np.ndarray:
    """
    Runs the flatbuffer one window at a time on TensorFlow's reference
    kernels, as the board runs one window at a time.
    @param model_bytes  A float32 TFLite flatbuffer.
    @param x            (m, window, channels) float32 windows.
    @return (m, classes) probabilities.
    """
    interp = _interpreter(model_bytes, reference=True)
    inp = interp.get_input_details()[0]
    out = interp.get_output_details()[0]
    rows = []
    for w in np.asarray(x, dtype=np.float32):
        interp.set_tensor(inp["index"], w.reshape(inp["shape"]))
        interp.invoke()
        rows.append(interp.get_tensor(out["index"]).astype(np.float32).reshape(-1))
    if not rows:
        return np.zeros((0, int(out["shape"][-1])), dtype=np.float32)
    return np.stack(rows)

# ============================================================================
# Functions: checks
# ============================================================================


def export_checks(facts: TfliteFacts, keras_probs: np.ndarray, tflite_probs: np.ndarray,
                  window: int, channels: int, n_classes: int, allowed_ops: tuple,
                  max_bytes: int, tol: float) -> list:
    """
    The export checks (E5). They protect the board, not the clips.
    @param facts         tflite_facts() of the flatbuffer.
    @param keras_probs   (m, n_classes) Keras outputs on the comparison windows.
    @param tflite_probs  (m, n_classes) TFLite outputs on the same windows.
    @param window        Rows per window (MODEL_WINDOW).
    @param channels      Channels per row.
    @param n_classes     Number of classes.
    @param allowed_ops   Ops the board's resolver registers.
    @param max_bytes     Flatbuffer must be smaller than this.
    @param tol           Largest allowed |Keras - TFLite| probability.
    @return One Check per check, in report order.
    """
    want_in = (1, window, channels)
    want_out = (1, n_classes)
    checks = [
        Check(CHECK_INPUT, facts.input_shape == want_in and facts.input_dtype == "float32",
              "%s %s (need %s float32)" % (facts.input_shape, facts.input_dtype, want_in)),
        Check(CHECK_OUTPUT, facts.output_shape == want_out and facts.output_dtype == "float32",
              "%s %s (need %s float32)" % (facts.output_shape, facts.output_dtype, want_out)),
    ]
    bad = sorted(set(facts.ops) - set(allowed_ops))
    checks.append(Check(CHECK_OPS, not bad,
                        ("outside the allowed list: %s" % ", ".join(bad)) if bad
                        else "%s" % ", ".join(sorted(set(facts.ops)))))
    checks.append(Check(CHECK_SIZE, facts.size < max_bytes,
                        "%d bytes (limit: under %d)" % (facts.size, max_bytes)))
    keras_probs = np.asarray(keras_probs, dtype=np.float64)
    tflite_probs = np.asarray(tflite_probs, dtype=np.float64)
    if keras_probs.shape != tflite_probs.shape or len(keras_probs) == 0:
        checks.append(Check(CHECK_MATCH, False, "no windows to compare (%s vs %s)"
                            % (keras_probs.shape, tflite_probs.shape)))
    else:
        diff = float(np.max(np.abs(keras_probs - tflite_probs)))
        same_top = int(np.sum(np.argmax(keras_probs, 1) == np.argmax(tflite_probs, 1)))
        m = len(keras_probs)
        checks.append(Check(CHECK_MATCH, same_top == m and diff <= tol,
                            "%d windows: same top class on %d, largest difference %.2e "
                            "(limit %.0e)" % (m, same_top, diff, tol)))
    return checks


def checks_passed(checks: list) -> bool:
    """@return True if every check passed."""
    return all(c.ok for c in checks)

# ============================================================================
# Functions: model_data.h
# ============================================================================


def _cfloat(v: float) -> str:
    """
    @param v  A finite float32 value.
    @return A C float literal that reads back as the same float32.
    @raise ValueError for inf or nan, which have no C float literal.
    """
    f = float(np.float32(v))
    if not np.isfinite(f):
        raise ValueError("model_data.h cannot hold %r" % f)
    s = "%.9g" % f
    if "." not in s and "e" not in s:
        s += ".0"
    return s + "f"


def model_data_header(info: HeaderInfo, model_bytes: bytes, test_x: np.ndarray,
                      test_p: np.ndarray, test_sources: list) -> str:
    """
    The text of model_data.h. No detection cutoff: DET_CONF lives only in
    config.h (P8).
    @param info          Run facts and the window contract.
    @param model_bytes   The float32 TFLite flatbuffer.
    @param test_x        (MODEL_TEST_N, window, channels) self-test windows.
    @param test_p        (MODEL_TEST_N, classes) Keras outputs for them.
    @param test_sources  One line per self-test window: where it came from.
    @return The file's text, LF line endings.
    """
    n_test = int(test_x.shape[0])
    n_cls = len(info.class_names)
    lines = [
        "// model_data.h: generated by firmware/cnn/training/train.py. Do not edit by hand;",
        "// run train.py again instead. Layout adapted from write_model_header() in",
        "// ml-on-device-playground (MIT, see firmware/cnn/training/"
        "LICENSE-ml-on-device-playground.txt).",
        "//",
        "// Model %s, generated %s, commit %s." % (info.model_version, info.generated,
                                                    info.commit),
    ]
    lines += ["// " + s for s in info.run_lines]
    lines += [
        "// Float32 TFLite, %d bytes. Ops: %s." % (len(model_bytes), ", ".join(info.ops)),
        "//",
        "// Input: one window of MODEL_WINDOW rows (oldest first) x MODEL_CHANNEL_COUNT",
        "// channels in MODEL_CHANNELS order, raw: z_hp in g, roll and pitch in dps, at",
        "// MODEL_RATE_HZ, exactly as dsp puts them in the L line. Scaling and",
        "// downsampling are inside the model (P1). Output: one probability per class, in",
        "// g_model_class_names order. The board runs it every MODEL_HOP samples.",
        "// The detection cutoff is DET_CONF in config.h, not here (P8).",
        "// Include from one .cpp file only: the arrays are static.",
        "",
        "#ifndef MODEL_DATA_H",
        "#define MODEL_DATA_H",
        "",
        "#define MODEL_VERSION        \"%s\"" % info.model_version,
        "#define MODEL_CLASS_COUNT    %d" % n_cls,
        "#define MODEL_WINDOW         %d" % info.window,
        "#define MODEL_HOP            %d" % info.hop,
        "#define MODEL_RATE_HZ        %d" % info.rate_hz,
        "#define MODEL_CHANNEL_COUNT  %d" % len(info.channels),
        "#define MODEL_CHANNELS       \"%s\"" % ",".join(info.channels),
        "#define MODEL_TEST_N         %d" % n_test,
        "",
        "static const char *const g_model_class_names[MODEL_CLASS_COUNT] = {",
    ]
    lines += ["  \"%s\"," % c for c in info.class_names]
    lines += ["};", "", "alignas(16) static const unsigned char g_model_data[] = {"]
    for i in range(0, len(model_bytes), BYTES_PER_LINE):
        chunk = model_bytes[i:i + BYTES_PER_LINE]
        lines.append("  " + " ".join("0x%02x," % b for b in chunk))
    lines += ["};", "static const unsigned int g_model_data_len = %d;" % len(model_bytes), ""]
    lines.append("// Boot self-test: MODEL_TEST_N windows (one per class) and the Keras outputs")
    lines.append("// for each. The board's outputs must match within SELFTEST_TOL (config.h).")
    lines += ["//   %d: %s" % (i, s) for i, s in enumerate(test_sources)]
    lines.append("static const float g_model_test_x[MODEL_TEST_N][MODEL_WINDOW]"
                 "[MODEL_CHANNEL_COUNT] = {")
    for w in test_x:
        lines.append("  {")
        lines += ["    {%s}," % ", ".join(_cfloat(v) for v in row) for row in w]
        lines.append("  },")
    lines.append("};")
    lines.append("static const float g_model_test_p[MODEL_TEST_N][MODEL_CLASS_COUNT] = {")
    lines += ["  {%s}," % ", ".join(_cfloat(v) for v in p) for p in test_p]
    lines += ["};", "", "#endif  // MODEL_DATA_H", ""]
    return "\n".join(lines)


def write_atomic(path: Path, text: str) -> None:
    """
    Writes text to path through a temporary file in the same folder, then
    swaps it in, so a crash never leaves a half-written file and the old file
    stays as it was until the new one is complete.
    @param path  Destination.
    @param text  Contents (LF line endings are kept).
    """
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
    os.replace(tmp, path)
