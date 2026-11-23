"""
File:        take_loader.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Loads take files for training (cnn-record-train-handoff.md v0.4,
             request 3, "Loader checks"). Reads <data_dir>/<session>/*.csv
             with the record GUI's own reader (take_store.load_take), so the
             GUI and training share one parser. It replaces the framework's
             load_and_preprocess_data, which flattened and zero-padded every
             take; nothing of that code is kept.

             Per file: UNLABELED takes are skipped and counted; empty,
             unreadable or unparseable files are skipped and listed; a take
             whose row count differs from its own header, whose label is not a
             class, whose name and header disagree, or that came from the
             simulated device (unless allowed) is rejected and listed.
             Per run: every accepted take must share the RUN_SETTINGS_KEYS
             header values and, among takes that kept their BOOT line, the
             BOOT_SETTINGS_KEYS values; otherwise the whole set is refused and
             the files that differ are named (MixedSettingsError).
             Needs numpy and the record GUI folder on sys.path.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

import cnn_config as cfg
from take_store import load_take, parse_take_name

# ============================================================================
# Constants
# ============================================================================

# fw of takes from the simulated device (sim_source.py), so they never pass as real.
SIM_FW_PREFIX = "sim-"

# ============================================================================
# Functions
# ============================================================================


def session_type(session: str) -> str:
    """
    @param session  Session name, e.g. 2026-11-14_car_route1.
    @return The first cfg.SESSION_TYPES entry that is one of the name's
            "_"-separated words ("car" here), or "other".
    """
    words = session.lower().split("_")
    for kind in cfg.SESSION_TYPES:
        if kind in words:
            return kind
    return "other"


def parse_boot(raw: str) -> dict:
    """
    @param raw  The take header's boot value: a BOOT line, or "missing (...)".
    @return Its key=value settings, or {} if it is not a BOOT line.
    """
    if not raw.startswith("BOOT,"):
        return {}
    settings = {}
    for part in raw.split(",")[1:]:
        key, sep, value = part.partition("=")
        if sep:
            settings[key] = value
    return settings


def list_sessions(data_dir: Path) -> list:
    """
    @param data_dir  Folder with one subfolder per session.
    @return Session folder names, sorted. Folders starting with "_" or "."
            are not sessions.
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return []
    return sorted(p.name for p in data_dir.iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")))


def thin_classes(takes: list, minimum: int = cfg.MIN_TAKES_PER_CLASS) -> list:
    """
    The minimum-takes check (C11): every class, in every session type that
    appears in the run, needs at least `minimum` takes.
    @param takes    Accepted Take objects.
    @param minimum  Takes needed per class per session type.
    @return (session_type, class, count) for each class below the minimum.
    """
    counts = Counter((t.session_type, t.label) for t in takes)
    thin = []
    for kind in sorted({t.session_type for t in takes}):
        for cls in cfg.CLASSES:
            n = counts.get((kind, cls), 0)
            if n < minimum:
                thin.append((kind, cls, n))
    return thin


def _rel(path: Path, root: Path) -> str:
    """
    @param path  A file under root.
    @param root  The data folder.
    @return path relative to root with "/" separators, or the full path.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _settings_text(keys: tuple, values: tuple) -> str:
    """
    @param keys    Setting names.
    @param values  Their values.
    @return "key=value, key=value, ...".
    """
    return ", ".join("%s=%s" % kv for kv in zip(keys, values))


def _mixed_message(what: str, keys: tuple, groups: dict, root: Path) -> str:
    """
    Names the files that differ: the largest group is summarized, every file
    of the other groups is listed.
    @param what    "header settings" or "BOOT settings".
    @param keys    The compared setting names.
    @param groups  settings tuple -> list of paths.
    @param root    The data folder (paths are shown relative to it).
    @return Lines of the refusal message.
    """
    ordered = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = ["The takes do not share one set of %s (%d sets found):" % (what, len(ordered))]
    for k, (values, paths) in enumerate(ordered):
        lines.append("  Set %d: %s: %d take(s)" % (k + 1, _settings_text(keys, values),
                                                   len(paths)))
        if k == 0:
            sessions = sorted({p.parent.name for p in paths})
            lines.append("    (the largest set; sessions %s)" % ", ".join(sessions))
        else:
            lines.extend("    %s" % _rel(p, root) for p in sorted(paths))
    return "\n".join(lines)

# ============================================================================
# Classes
# ============================================================================


@dataclass
class Take:
    """One accepted take."""
    path: Path
    session: str
    label: str        # One of cfg.CLASSES
    x: np.ndarray     # (n_rows, n_channels) float32, as recorded (g, dps)
    header: dict      # Every "# key=value" header line

    @property
    def session_type(self) -> str:
        """@return "bench", "car" or "other", from the session name."""
        return session_type(self.session)

    @property
    def rule_class(self) -> str:
        """@return The rule-based class as saved (POTHOLE, BUMP, UNKNOWN or missing)."""
        return self.header.get("rule_class", "missing")


@dataclass
class LoadReport:
    """What the loader accepted, skipped and rejected, for the run log."""
    data_dir: Path
    takes: list = field(default_factory=list)          # Take
    unlabeled: list = field(default_factory=list)      # Path
    skipped: list = field(default_factory=list)        # (Path, reason): empty, unparseable, ...
    rejected: list = field(default_factory=list)       # (Path, reason): wrong length, label, ...
    settings: dict = field(default_factory=dict)       # The run's RUN_SETTINGS_KEYS values
    boot_settings: dict = field(default_factory=dict)  # BOOT_SETTINGS_KEYS values, if seen
    n_with_boot: int = 0                               # Takes that kept their BOOT line

    def summary_lines(self) -> list:
        """
        @return (level, text) pairs for the run log: "info" or "warning".
        """
        root = Path(self.data_dir)
        out = []
        sessions = sorted({t.session for t in self.takes})
        out.append(("info", "Accepted %d take(s) from %d session(s)" % (len(self.takes),
                                                                       len(sessions))))
        counts = Counter((t.session, t.label) for t in self.takes)
        for s in sessions:
            per = ", ".join("%s %d" % (c, counts.get((s, c), 0)) for c in cfg.CLASSES)
            out.append(("info", "  %s (%s): %s" % (s, session_type(s), per)))
        if self.unlabeled:
            out.append(("warning", "Skipped %d UNLABELED take(s) (relabel them in the record "
                                   "GUI to use them)" % len(self.unlabeled)))
        if self.skipped:
            out.append(("warning", "Skipped %d file(s):" % len(self.skipped)))
            out.extend(("warning", "  %s: %s" % (_rel(p, root), why)) for p, why in self.skipped)
        if self.rejected:
            out.append(("warning", "Rejected %d take(s):" % len(self.rejected)))
            out.extend(("warning", "  %s: %s" % (_rel(p, root), why))
                       for p, why in self.rejected)
        if self.settings:
            out.append(("info", "Settings: %s" % _settings_text(
                tuple(self.settings), tuple(self.settings.values()))))
        if self.boot_settings:
            out.append(("info", "BOOT settings (from %d of %d takes): %s" % (
                self.n_with_boot, len(self.takes), _settings_text(
                    tuple(self.boot_settings), tuple(self.boot_settings.values())))))
        elif self.takes:
            out.append(("warning", "No take kept its BOOT line: the preprocessing settings are "
                                   "known only through fw=%s" % self.settings.get("fw", "?")))
        return out


class MixedSettingsError(ValueError):
    """The accepted takes do not share one settings set; the run is refused."""

    def __init__(self, message: str, report: LoadReport) -> None:
        """
        @param message  Names the differing files.
        @param report   What was loaded before the refusal (skips, rejections).
        """
        super().__init__(message)
        self.report = report

# ============================================================================
# Loader
# ============================================================================


def _check_file(path: Path, session: str, allow_sim: bool, report: LoadReport) -> Optional[Take]:
    """
    Reads and checks one take file. Records a skip or rejection in report.
    @param path       A file in a session folder.
    @param session    Its session name.
    @param allow_sim  Accept takes from the simulated device.
    @param report     Collects skips and rejections.
    @return The Take, or None if it was skipped or rejected.
    """
    parsed = parse_take_name(path)
    if parsed is None:
        report.skipped.append((path, "not a take file name (<LABEL>-<n>.csv)"))
        return None
    name_label = parsed[0]
    if name_label == cfg.UNLABELED:
        report.unlabeled.append(path)   # Counted without reading: relabeled later
        return None

    def skip(why: str) -> None:
        report.skipped.append((path, why))

    def reject(why: str) -> None:
        report.rejected.append((path, why))

    try:
        if path.stat().st_size == 0:
            skip("empty file")
            return None
        header, values = load_take(path)
    except ValueError as exc:            # Includes a file that is not UTF-8 text
        skip("unparseable: %s" % exc)
        return None
    except OSError as exc:
        skip("cannot read: %s" % exc)
        return None
    if not values:
        skip("no data rows")
        return None
    missing = [k for k in cfg.RUN_SETTINGS_KEYS if k not in header]
    if missing:
        skip("header lacks %s" % ", ".join(missing))
        return None

    label = header.get("label", "")
    if label != name_label:
        reject("label in the file name (%s) and in the header (%s) differ" % (name_label, label))
        return None
    if label not in cfg.CLASSES:
        reject("label %s is not one of %s" % (label, ", ".join(cfg.CLASSES)))
        return None
    if header["fw"].startswith(SIM_FW_PREFIX) and not allow_sim:
        reject("simulated take (fw=%s); allow simulated takes to use it" % header["fw"])
        return None
    try:
        n_rows, n_pre, n_post = (int(header[k]) for k in ("n_rows", "n_pre", "n_post"))
    except ValueError:
        skip("header n_rows / n_pre / n_post are not whole numbers")
        return None
    if n_rows != n_pre + n_post:
        reject("header n_rows %d is not n_pre + n_post (%d + %d)" % (n_rows, n_pre, n_post))
        return None
    n_ch = len(header["channels"].split("/"))
    widths = {len(row) for row in values}
    if widths != {n_ch}:
        reject("rows must have %d values (channels %s), found %s" % (
            n_ch, header["channels"], "/".join(str(w) for w in sorted(widths))))
        return None
    if len(values) != n_rows:
        reject("wrong length: %d rows, header says %d" % (len(values), n_rows))
        return None
    x = np.asarray(values, dtype=np.float32)
    if not np.all(np.isfinite(x)):
        reject("holds a value that is not a finite number")
        return None
    return Take(path=path, session=session, label=label, x=x, header=header)


def load_dataset(data_dir: Path, sessions: Optional[list] = None,
                 allow_sim: bool = False) -> LoadReport:
    """
    Loads every take of the chosen sessions and checks they form one dataset.
    @param data_dir   Folder with one subfolder per session.
    @param sessions   Session names to use, or None for every session folder.
    @param allow_sim  Accept takes from the simulated device (fw "sim-...").
    @return The LoadReport; report.takes may be empty.
    @raise FileNotFoundError if data_dir is not a folder.
    @raise ValueError if a named session folder does not exist.
    @raise MixedSettingsError if the accepted takes do not share one settings set.
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError("Data folder not found: %s" % data_dir)
    available = list_sessions(data_dir)
    if sessions:
        unknown = sorted(set(sessions) - set(available))
        if unknown:
            raise ValueError("No session folder %s in %s" % (", ".join(unknown), data_dir))
        chosen = sorted(set(sessions))
    else:
        chosen = available

    report = LoadReport(data_dir=data_dir)
    for session in chosen:
        for path in sorted((data_dir / session).iterdir()):
            # Subfolders (_deleted) are not read; neither are the GUI's
            # temporary files (".<name>.tmp") or anything that is not a CSV.
            if path.is_dir() or path.name.startswith(".") or path.suffix.lower() != ".csv":
                continue
            take = _check_file(path, session, allow_sim, report)
            if take is not None:
                report.takes.append(take)

    # One settings set per run (C7): the SNB settings and fw in every header ...
    groups = defaultdict(list)
    for t in report.takes:
        groups[tuple(t.header[k] for k in cfg.RUN_SETTINGS_KEYS)].append(t.path)
    # ... and the BOOT settings, among the takes that kept their BOOT line.
    boot_groups = defaultdict(list)
    for t in report.takes:
        boot = parse_boot(t.header.get("boot", ""))
        if boot:
            boot_groups[tuple(boot.get(k, "?") for k in cfg.BOOT_SETTINGS_KEYS)].append(t.path)

    problems = []
    if len(groups) > 1:
        problems.append(_mixed_message("header settings", cfg.RUN_SETTINGS_KEYS, groups,
                                       data_dir))
    if len(boot_groups) > 1:
        problems.append(_mixed_message("BOOT settings", cfg.BOOT_SETTINGS_KEYS, boot_groups,
                                       data_dir))
    if problems:
        raise MixedSettingsError(
            "Refusing a mixed set (handoff, \"Loader checks\"): any settings change starts a "
            "new dataset. Pick the sessions of one set, or move the odd takes out.\n"
            + "\n".join(problems), report)

    if groups:
        report.settings = dict(zip(cfg.RUN_SETTINGS_KEYS, next(iter(groups))))
    if boot_groups:
        values, paths = next(iter(boot_groups.items()))
        report.boot_settings = dict(zip(cfg.BOOT_SETTINGS_KEYS, values))
        report.n_with_boot = len(paths)
    return report
