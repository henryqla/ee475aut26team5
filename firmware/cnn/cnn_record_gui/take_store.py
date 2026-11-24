"""
File:        take_store.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B): files
             are clips (clip_file.py builds their text). Removed with the
             snapshots, the cutter and the drive log: save_take(), src_keys(),
             RULE_FIELDS and the manual/, weak/ and background/ subfolders
             (git tag pre-manual-overhaul). Labels are the three classes (O4);
             older takes named FALSE_TRIGGER or UNLABELED are still listed,
             loaded, relabeled and deleted. New: write_take_streamed() writes a
             long recording from its rows file without holding it in memory.
             relabel() now copies line by line, for the same reason.
             0.2.0: one shared writer for every take kind; read_header().
Adapted from: the save / load / delete logic of Python/collected_data_widget.py
             in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Rewritten:
             CSV with a header instead of np.savetxt, session folders, relabel,
             and atomic writes.
Description: Clip files (C9, DECIDED): one CSV per clip, one folder per
             session, at <DATA_DIR>/<session>/<LABEL>-<n>.csv. Each file starts
             with "# key=value" header lines, then one row per sample.
             Standard library only.

             Writes go to a temporary file that is then renamed, so a crash or
             a pulled cable never leaves a half-written clip. Numbers are never
             reused within a session (deleted clips count), and delete moves a
             clip into <session>/_deleted/ rather than destroying it. Nothing
             here deletes, hides or changes a clip on its own (O3): every
             relabel and delete is the owner's action in the GUI.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import datetime
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import cnn_config as cfg

# ============================================================================
# Constants
# ============================================================================

# Session folder names: letters, digits, '_' and '-', e.g. 2026-11-14_car_route1.
_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# Names Windows will not use for a folder.
_WINDOWS_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {"COM%d" % i for i in range(1, 10)} \
    | {"LPT%d" % i for i in range(1, 10)}

# Copy buffer for long files (bytes).
_COPY_CHUNK = 1 << 20

# ============================================================================
# Functions
# ============================================================================


def checkout_commit(path: Path) -> str:
    """
    The commit of the git checkout holding path, for clip headers. This is
    the PC's checkout, not necessarily what is flashed: the BOOT line in the
    header identifies the firmware.
    @param path  Any path inside the repository.
    @return The commit hash, with "-dirty" if there are uncommitted changes,
            or "unknown" if git is not available.
    """
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path, capture_output=True,
                              text=True, timeout=5, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain"], cwd=path,
                               capture_output=True, text=True, timeout=5,
                               check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return head + ("-dirty" if dirty else "")


def iso_time(wall: float) -> str:
    """
    @param wall  Seconds since the epoch (time.time()).
    @return Local time with its UTC offset, ms resolution, ISO 8601; or the
            raw epoch seconds if the platform cannot convert it (Windows
            refuses times before 1970), so a clip is never lost over a
            timestamp.
    """
    try:
        return datetime.datetime.fromtimestamp(wall).astimezone().isoformat(
            timespec="milliseconds")
    except (OSError, OverflowError, ValueError):
        return "%.3f (epoch s)" % wall


def _clean(value) -> str:
    """
    @param value  Any header value.
    @return Its text on one line (a header value must not contain a newline).
    """
    return str(value).replace("\r", " ").replace("\n", " ")


def header_text(pairs: list) -> str:
    """
    The "# key=value" header of a clip file.
    @param pairs  (key, value) in file order.
    @return The header lines, each ending in a newline.
    """
    return "".join("# %s=%s\n" % (k, _clean(v)) for k, v in pairs)


def read_header(path: Path) -> dict:
    """
    Reads only the "# key=value" lines at the top of a clip or take file,
    stopping at the first data row (cheap for listing many files).
    @param path  The file.
    @return Each header key mapped to its value.
    @raise OSError if the file cannot be read.
    """
    header = {}
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if not line.startswith("#"):
                break
            key, sep, value = line[1:].strip().partition("=")
            if sep:
                header[key] = value
    return header


def parse_take_name(path: Path) -> Optional[tuple]:
    """
    @param path  A clip or take file path.
    @return (label, n) from <LABEL>-<n>.csv, or None if the name is not one.
    """
    if path.suffix != ".csv":
        return None
    label, sep, number = path.stem.rpartition("-")
    if not sep or not label or not number.isdigit():
        return None
    return label, int(number)


def load_take(path: Path) -> tuple:
    """
    Reads a clip or take file with the standard library (small files and
    tests; the GUI and the window cutter use clip_file.load_clip()).
    @param path  The file.
    @return (header, values): header maps each "# key=value" key to its
            value; values is a list of float tuples, one per row.
    @raise ValueError if a data row does not parse.
    @raise OSError if the file cannot be read.
    """
    header = {}
    values = []
    with open(path, "r", encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith("#"):
                key, sep, value = line[1:].strip().partition("=")
                if sep:
                    header[key] = value
                continue
            try:
                values.append(tuple(float(x) for x in line.split(",")))
            except ValueError:
                raise ValueError("%s line %d: not a data row: %r" % (path.name, n, line))
    return header, values

# ============================================================================
# Classes
# ============================================================================


class TakeStore:
    """Creates, lists, relabels and deletes clip files under one data folder."""

    def __init__(self, data_dir: Path = cfg.DATA_DIR) -> None:
        """
        @param data_dir  Folder that holds one subfolder per session.
        """
        self.data_dir = Path(data_dir)

    @staticmethod
    def validate_session(name: str) -> None:
        """
        @param name  Session name, used as a folder name.
        @raise ValueError if it is not a safe folder name.
        """
        if not _SESSION_RE.match(name or "") or name.upper() in _WINDOWS_RESERVED:
            raise ValueError("Session name %r: use letters, digits, '_' and '-' "
                             "(e.g. 2026-11-14_car_route1)" % name)

    @staticmethod
    def validate_label(label: str) -> None:
        """
        @param label  Clip label.
        @raise ValueError if it is not one of cfg.LABELS.
        """
        if label not in cfg.LABELS:
            raise ValueError("Label %r is not one of %s" % (label, ", ".join(cfg.LABELS)))

    def session_dir(self, session: str) -> Path:
        """
        @param session  A valid session name.
        @return Its folder (not created).
        """
        self.validate_session(session)
        return self.data_dir / session

    def sessions(self) -> list:
        """
        @return Names of the existing session folders, sorted.
        """
        if not self.data_dir.is_dir():
            return []
        return sorted(p.name for p in self.data_dir.iterdir()
                      if p.is_dir() and _SESSION_RE.match(p.name))

    def list_takes(self, session: str) -> list:
        """
        @param session  A valid session name.
        @return The session's clip and take files, oldest first (relabel
                keeps the file time). Any <LABEL>-<n>.csv name counts, so
                older takes labeled FALSE_TRIGGER or UNLABELED are listed too.
        """
        folder = self.session_dir(session)
        if not folder.is_dir():
            return []
        takes = [p for p in folder.glob("*.csv") if parse_take_name(p) is not None]
        return sorted(takes, key=lambda p: (p.stat().st_mtime, p.name))

    def _next_number(self, folder: Path, label: str) -> int:
        """
        @param folder  A session folder.
        @param label   A label.
        @return One more than the highest number used for this label in the
                session, deleted clips included, so a number is never reused.
        """
        highest = 0
        for sub in (folder, folder / cfg.DELETED_DIR_NAME):
            if not sub.is_dir():
                continue
            for p in sub.glob("%s-*.csv" % label):
                parsed = parse_take_name(p)
                if parsed is not None and parsed[0] == label:
                    highest = max(highest, parsed[1])
        return highest + 1

    def _new_path(self, folder: Path, label: str) -> Path:
        """
        @param folder  A session folder (exists).
        @param label   A label.
        @return The next free <label>-<n>.csv path in it.
        """
        n = self._next_number(folder, label)
        path = folder / ("%s-%d.csv" % (label, n))
        while path.exists():  # Only if a file appeared since the scan
            n += 1
            path = folder / ("%s-%d.csv" % (label, n))
        return path

    @staticmethod
    def _tmp_for(path: Path) -> Path:
        """
        @param path  Final file path.
        @return The temporary file it is written to first (hidden, not *.csv).
        """
        return path.with_name("." + path.name + ".tmp")

    @classmethod
    def _write_atomic(cls, path: Path, text: str) -> None:
        """
        Writes text to a temporary file next to path, then renames it into
        place, so path is either absent or complete.
        @param path  Final file path.
        @param text  Full file contents.
        """
        tmp = cls._tmp_for(path)
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def write_take(self, session: str, label: str, text: str) -> Path:
        """
        Writes one finished clip under the next free number:
        <session>/<LABEL>-<n>.csv.
        @param session  Session name.
        @param label    Its label (cfg.LABELS).
        @param text     Full file contents (header and rows).
        @return The new file's path.
        @raise ValueError for a bad session or label; OSError if writing fails.
        """
        self.validate_label(label)
        folder = self.session_dir(session)
        folder.mkdir(parents=True, exist_ok=True)
        path = self._new_path(folder, label)
        self._write_atomic(path, text)
        return path

    def write_take_streamed(self, session: str, label: str, head: str, body_path: Path) -> Path:
        """
        Writes one clip whose rows are in a file (a long recording), copying
        them in chunks so the recording is never held in memory. The rows
        file is left in place; the caller removes it once this succeeded.
        @param session    Session name.
        @param label      Its label (cfg.LABELS).
        @param head       The "# key=value" header text.
        @param body_path  File holding the data rows, each ending in "\\n".
        @return The new file's path.
        @raise ValueError for a bad session or label; OSError if writing fails.
        """
        self.validate_label(label)
        folder = self.session_dir(session)
        folder.mkdir(parents=True, exist_ok=True)
        path = self._new_path(folder, label)
        tmp = self._tmp_for(path)
        with open(tmp, "w", encoding="utf-8", newline="\n") as out:
            out.write(head)
            with open(body_path, "r", encoding="utf-8", newline="") as src:
                shutil.copyfileobj(src, out, _COPY_CHUNK)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
        return path

    def relabel(self, path: Path, new_label: str) -> Path:
        """
        Moves a clip under a new label: new name <NEW>-<n>.csv in the same
        session, header label updated and the change recorded. Keeps the
        original file time, so the clip stays in recording order. Works for
        older takes too (any old label).
        @param path       Existing clip file.
        @param new_label  One of cfg.LABELS.
        @return The new path (path itself if the label is unchanged).
        @raise ValueError for a bad label or file name; OSError if moving fails.
        """
        self.validate_label(new_label)
        parsed = parse_take_name(path)
        if parsed is None:
            raise ValueError("Not a clip file: %s" % path.name)
        old_label = parsed[0]
        if old_label == new_label:
            return path

        new_path = self._new_path(path.parent, new_label)
        tmp = self._tmp_for(new_path)
        st = path.stat()
        stamp = iso_time(datetime.datetime.now().timestamp())
        # Line by line, so a long recording is never held in memory.
        with open(path, "r", encoding="utf-8", newline="") as src, \
                open(tmp, "w", encoding="utf-8", newline="\n") as out:
            for line in src:
                if line.startswith("# label="):
                    out.write("# label=%s\n" % new_label)
                    out.write("# relabeled=%s>%s at %s\n" % (old_label, new_label, stamp))
                else:
                    out.write(line)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, new_path)
        os.utime(new_path, (st.st_atime, st.st_mtime))
        os.remove(path)  # After the new file is complete: a crash leaves both, never neither
        return new_path

    def delete(self, path: Path) -> Path:
        """
        Moves a clip into its session's _deleted folder.
        @param path  Existing clip file.
        @return Where it went.
        @raise OSError if moving fails.
        """
        trash = path.parent / cfg.DELETED_DIR_NAME
        trash.mkdir(exist_ok=True)
        dest = trash / path.name
        k = 1
        while dest.exists():
            dest = trash / ("%s.dup%d%s" % (path.stem, k, path.suffix))
            k += 1
        os.replace(path, dest)
        return dest
