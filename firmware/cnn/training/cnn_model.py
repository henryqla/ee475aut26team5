"""
File:        cnn_model.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Adapted from: create_model() (the "1D CNN" branch) in
             Python/training_widget.py of ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             layer stack (Conv1D 32 k3 ReLU, MaxPool 2, Conv1D 32 k3 ReLU,
             MaxPool 2, Flatten, Dense 16 ReLU, Dense softmax), Adam and the
             sparse categorical cross-entropy loss. Changed: the input is
             (n_rows, 3), time by channel, instead of a flattened take
             reshaped to (300, 1), which slid the convolution across
             channels; a Normalization layer comes first (C10); layer sizes
             come from cnn_config.
Description: Builds the event CNN. Normalization is inside the graph (C10,
             owner 2026-11-22): per-channel mean and variance from the
             training takes only are frozen into the model, so the firmware
             feeds raw g and dps and has nothing else to match. Imports
             TensorFlow: train.py imports this module only after checking
             that TensorFlow is installed.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import numpy as np
import tensorflow as tf

import cnn_config as cfg

# ============================================================================
# Constants
# ============================================================================

# A channel whose variance is below this (a constant channel) is divided by 1
# instead, so normalization never divides by zero.
MIN_VARIANCE = 1e-12

# ============================================================================
# Functions
# ============================================================================


def normalization_stats(x_train: np.ndarray) -> tuple:
    """
    Per-channel mean and variance over every row of the training takes.
    Called with the original training takes, not the oversampled copies.
    @param x_train  (n_takes, n_rows, n_channels) training inputs.
    @return (mean, variance), float32 arrays of n_channels.
    """
    flat = x_train.reshape(-1, x_train.shape[-1]).astype(np.float64)
    mean = flat.mean(axis=0)
    var = flat.var(axis=0)
    var = np.where(var < MIN_VARIANCE, 1.0, var)
    return mean.astype(np.float32), var.astype(np.float32)


def build_model(input_shape: tuple, n_classes: int, mean: np.ndarray,
                variance: np.ndarray) -> tf.keras.Model:
    """
    @param input_shape  (n_rows, n_channels), e.g. (624, 3).
    @param n_classes    Number of output classes.
    @param mean         Per-channel mean (normalization_stats).
    @param variance     Per-channel variance (normalization_stats).
    @return The compiled Keras model; its output is one probability per class.
    """
    layers = tf.keras.layers
    model = tf.keras.Sequential(name="event_cnn")
    model.add(tf.keras.Input(shape=tuple(input_shape), name="snapshot"))
    # (x - mean) / sqrt(variance) per channel, with the training statistics
    # frozen in (C10). Becomes two element-wise ops in the TFLite model.
    model.add(layers.Normalization(axis=-1, mean=mean, variance=variance, name="normalize"))
    for i, filters in enumerate(cfg.CNN_CONV_FILTERS, 1):
        model.add(layers.Conv1D(filters, cfg.CNN_KERNEL_SIZE, activation="relu",
                                name="conv%d" % i))
        model.add(layers.MaxPooling1D(cfg.CNN_POOL_SIZE, name="pool%d" % i))
    model.add(layers.Flatten(name="flatten"))
    model.add(layers.Dense(cfg.CNN_DENSE_UNITS, activation="relu", name="dense"))
    model.add(layers.Dense(n_classes, activation="softmax", name="probs"))
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=cfg.TRAIN_LEARNING_RATE),
                  loss="sparse_categorical_crossentropy",
                  metrics=["accuracy"])
    return model
