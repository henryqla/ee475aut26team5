"""
File:        take_store.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Adapted from: the save / load / delete logic of Python/collected_data_widget.py
             in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Rewritten:
             CSV with a header instead of np.savetxt, session folders, relabel,
             and atomic writes.
Description: Take files (C9, DECIDED): one CSV per take, one folder per
             session, at <DATA_DIR>/<session>/<LABEL>-<n>.csv. Each file starts
             with "# key=value" header lines (every SNB setting, the matching
             EVT's rule class and peaks, the BOOT line, the PC time and the
             commit), then one row per sample, "c0,c1,c2", exactly as the
             firmware printed them. np.loadtxt(path, delimiter=",") reads the
             rows directly ('#' lines are comments). Standard library only.

             Writes go to a temporary file that is then renamed, so a crash or
             a pulled cable never leaves a half-written take. Numbers are never
             reused within a session (deleted takes count), and Delete moves a
             take into <session>/_deleted/ rather than destroying it.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import datetime
import os
import re
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

# EVT fields copied into the take header as rule_<name> (the rule baseline).
_RULE_FIELDS = ("class", "peak_z_g", "peak_roll_dps", "peak_pitch_dps", "pitch_seq",
                "basis", "n_peak", "t_us_peak")

# ============================================================================
# Functions
# ============================================================================


def checkout_commit(path: Path) -> str:
    """
    The commit of the git checkout holding path, for take headers. This is
    the PC's checkout, not necessarily what is flashed: the take's fw field
    identifies the firmware.
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


def _iso(wall: float) -> str:
    """
    @param wall  Seconds since the epoch (time.time()).
    @return Local time with its UTC offset, ms resolution, ISO 8601; or the
            raw epoch seconds if the platform cannot convert it (Windows
            refuses times before 1970), so a take is never lost over a
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


def parse_take_name(path: Path) -> Optional[tuple]:
    """
    @param path  A take file path.
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
    Reads a take file.
    @param path  The take file.
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
    """Creates, lists, relabels and deletes take files under one data folder."""

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
        @param label  Take label.
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
        @return The session's take files, oldest first (relabel keeps the time).
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
                session, deleted takes included, so a number is never reused.
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

    @staticmethod
    def _write_atomic(path: Path, text: str) -> None:
        """
        Writes text to a temporary file next to path, then renames it into
        place, so path is either absent or complete.
        @param path  Final file path.
        @param text  Full file contents.
        """
        tmp = path.with_name("." + path.name + ".tmp")
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)

    def save_take(self, session: str, label: str, snap, evt, boot_raw: Optional[str],
                  gui_commit: str, received_wall: float, evt_missing_reason: str = "") -> Path:
        """
        Writes one take.
        @param session             Session name.
        @param label               Take label.
        @param snap                snapshot_protocol.Snapshot.
        @param evt                 Its snapshot_protocol.EventLine, or None if missing.
        @param boot_raw            Latest BOOT line on this connection, or None.
        @param gui_commit          Commit of this checkout.
        @param received_wall       Wall time the snapshot arrived (time.time()).
        @param evt_missing_reason  Why evt is None, recorded in the header.
        @return The new file's path.
        @raise ValueError for a bad session or label; OSError if writing fails.
        """
        self.validate_label(label)
        folder = self.session_dir(session)
        folder.mkdir(parents=True, exist_ok=True)

        # The trigger came about n_post samples before the snapshot finished
        # filling; the send time is not subtracted, so this is an estimate.
        trigger_wall = received_wall - snap.n_post / float(snap.rate_hz)
        lines = [
            ("take_format", cfg.TAKE_FORMAT_VERSION),
            ("label", label),
            ("session", session),
            ("pc_received", _iso(received_wall)),
            ("pc_trigger_est", _iso(trigger_wall)),
            ("gui_commit", gui_commit),
            ("fw", snap.fw),
            ("seq", snap.seq),
            ("trig_idx", snap.trig_idx),
            ("rate_hz", snap.rate_hz),
            ("n_pre", snap.n_pre),
            ("n_post", snap.n_post),
            ("n_rows", snap.n_rows),
            ("channels", snap.channels),
            ("crc16", snap.crc16),
        ]
        for name in _RULE_FIELDS:
            value = evt.fields.get(name, "missing") if evt is not None else "missing"
            lines.append(("rule_" + name, value))
        lines.append(("evt", evt.raw if evt is not None
                      else "missing (%s)" % (evt_missing_reason or "no EVT")))
        lines.append(("boot", boot_raw if boot_raw else "missing (not seen on this connection)"))
        lines.append(("columns", snap.channels.replace("/", ",")))

        text = "".join("# %s=%s\n" % (k, _clean(v)) for k, v in lines)
        text += "".join("%s,%s,%s\n" % row for row in snap.rows)

        n = self._next_number(folder, label)
        path = folder / ("%s-%d.csv" % (label, n))
        while path.exists():  # Only if a file appeared since the scan
            n += 1
            path = folder / ("%s-%d.csv" % (label, n))
        self._write_atomic(path, text)
        return path

    def relabel(self, path: Path, new_label: str) -> Path:
        """
        Moves a take under a new label: new name <NEW>-<n>.csv in the same
        session, header label updated and the change recorded. Keeps the
        original file time, so the take stays in recording order.
        @param path       Existing take file.
        @param new_label  One of cfg.LABELS.
        @return The new path (path itself if the label is unchanged).
        @raise ValueError for a bad label or file name; OSError if moving fails.
        """
        self.validate_label(new_label)
        parsed = parse_take_name(path)
        if parsed is None:
            raise ValueError("Not a take file: %s" % path.name)
        old_label = parsed[0]
        if old_label == new_label:
            return path

        out = []
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("# label="):
                    out.append("# label=%s\n" % new_label)
                    out.append("# relabeled=%s>%s at %s\n" % (
                        old_label, new_label, _iso(datetime.datetime.now().timestamp())))
                else:
                    out.append(line)

        folder = path.parent
        n = self._next_number(folder, new_label)
        new_path = folder / ("%s-%d.csv" % (new_label, n))
        while new_path.exists():
            n += 1
            new_path = folder / ("%s-%d.csv" % (new_label, n))
        st = path.stat()
        self._write_atomic(new_path, "".join(out))
        os.utime(new_path, (st.st_atime, st.st_mtime))
        os.remove(path)  # After the new file is complete: a crash leaves both, never neither
        return new_path

    def delete(self, path: Path) -> Path:
        """
        Moves a take into its session's _deleted folder.
        @param path  Existing take file.
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
