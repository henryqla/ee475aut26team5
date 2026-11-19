"""
File:        main.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Adapted from: Python/main.py in ml-on-device-playground (created by
             Anandghan), https://github.com/jake-g/ml-on-device-playground
             (branch codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Added: the
             import path for cnn_config, the log file, and an exception hook.
Description: Starts the CNN record GUI: python main.py (from this folder).
             Logs to firmware/cnn/logs/record_gui_<date>.log. Unexpected
             errors are logged instead of closing the window (PyQt5 aborts on
             an unhandled exception in a slot unless sys.excepthook is
             replaced), so a car session keeps running.
"""

# ============================================================================
# Imports
# ============================================================================

import datetime
import logging
import sys
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))  # firmware/cnn, for cnn_config

import cnn_config as cfg  # noqa: E402  (needs the path above)
from PyQt5.QtWidgets import QApplication  # noqa: E402
from main_window import MainWindow  # noqa: E402
from take_store import checkout_commit  # noqa: E402

# ============================================================================
# Functions
# ============================================================================


def _log_exception(exc_type, exc, tb) -> None:
    """
    sys.excepthook: logs an unexpected error and keeps the GUI running.
    @param exc_type  Exception class.
    @param exc       Exception.
    @param tb        Traceback.
    """
    logging.getLogger("record").error("Unexpected error:\n%s",
                                      "".join(traceback.format_exception(exc_type, exc, tb)))


def main() -> None:
    """Sets up logging, then runs the window until it closes."""
    cfg.LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = cfg.LOG_DIR / ("record_gui_%s.log" % datetime.date.today().isoformat())
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.FileHandler(log_file, encoding="utf-8"),
                                  logging.StreamHandler()])
    sys.excepthook = _log_exception
    commit = checkout_commit(HERE)
    logging.getLogger("record").info("Record GUI start, commit %s, data %s, log %s",
                                     commit, cfg.DATA_DIR, log_file)

    app = QApplication(sys.argv)
    window = MainWindow(commit)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
