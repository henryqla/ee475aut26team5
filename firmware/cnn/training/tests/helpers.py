"""
File:        helpers.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Shared test helpers for the training code. Puts the training
             folder, firmware/cnn (cnn_config) and the record GUI folder
             (take_store, snapshot_protocol) on sys.path, and says whether
             TensorFlow is installed (tests that need it are skipped if not).
"""

# ============================================================================
# Imports
# ============================================================================

import importlib.util
import sys
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parents[1]
CNN_DIR = TRAINING_DIR.parent
for _p in (TRAINING_DIR, CNN_DIR, CNN_DIR / "cnn_record_gui"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# ============================================================================
# Functions
# ============================================================================


def tensorflow_available() -> bool:
    """
    @return True if TensorFlow can be imported (without importing it).
    """
    return importlib.util.find_spec("tensorflow") is not None
