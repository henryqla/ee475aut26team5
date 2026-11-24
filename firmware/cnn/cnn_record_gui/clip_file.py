"""
File:        clip_file.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.0
Change:      0.1.0 (manual-recording-overhaul-handoff.md, request B): new file.
Description: The clip file format, clip_v1 (handoff, "Clip file"), in one
             place, for the record GUI and the training window cutter
             (firmware/cnn/training/windows.py), so both read clips with the
             same code.
             - ClipFacts counts plain facts about a clip's rows as they arrive:
               rows, missing samples (jumps in n), resets (n going down) and
               ok=0 rows. Facts never block, delete, color or tag a clip (O3).
             - clip_header() builds the "# key=value" header.
             - load_clip() reads a clip_v1 file, or an older 624-row
               snapshot-era take (three columns, z_hp / roll / pitch), with
               numpy, never changing the file.
             - info_lines() turns a header into the plain lines the GUI's info
               panel shows.
             A clip row is the L line's 12 fields without the tag, exactly as
             received: n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok.
             Needs numpy (installed with the GUI); no Qt.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

import cnn_config as cfg
from snapshot_protocol import L_FIELDS
from take_store import header_text, iso_time, parse_take_name, read_header

# ============================================================================
# Constants
# ============================================================================

# Columns of a clip row (the L line's fields after the tag).
CLIP_COLUMNS = L_FIELDS

# Column indexes load_clip() reads from a clip: n, z_hp, roll, pitch, ok.
_CLIP_USECOLS = (L_FIELDS.index("n"), L_FIELDS.index("z_hp"), L_FIELDS.index("roll"),
                 L_FIELDS.index("pitch"), L_FIELDS.index("ok"))

# LoadedClip.kind values.
KIND_CLIP = "clip"   # format=clip_v1
KIND_TAKE = "take"   # An older snapshot-era take (take_format=1, 624 rows)

# Header value for a field that has no value (e.g. press_n of an empty clip).
NONE_TEXT = "none"

# ============================================================================
# Classes
# ============================================================================


class ClipFacts:
    """Plain facts about a clip's rows, counted one row at a time."""

    def __init__(self) -> None:
        self.rows = 0                     # Rows received
        self.missing = 0                  # Samples skipped: sum of the jumps in n
        self.resets = 0                   # Times n did not go up (the board reset)
        self.ok0 = 0                      # Rows with ok = 0
        self.span = 0                     # Device samples covered: rows + missing
        self.first_n: Optional[int] = None
        self.last_n: Optional[int] = None

    def add(self, n: int, ok: bool) -> int:
        """
        Counts one row.
        @param n   Its sample count.
        @param ok  Its ok flag.
        @return How many device samples this row moved the clip on: 1, plus
                the samples missing just before it (1 after a reset).
        """
        step = 1
        if self.last_n is None:
            self.first_n = n
        elif n > self.last_n + 1:
            self.missing += n - self.last_n - 1
            step = n - self.last_n
        elif n <= self.last_n:
            self.resets += 1
        self.last_n = n
        self.rows += 1
        self.span += step
        if not ok:
            self.ok0 += 1
        return step


@dataclass
class LoadedClip:
    """A clip (or older take) read from disk."""
    path: Path
    header: dict
    kind: str                         # KIND_CLIP or KIND_TAKE
    label: str                        # From the file name (follows relabels)
    rate_hz: float
    model: np.ndarray                 # (rows, 3) float64: z_hp (g), roll, pitch (dps)
    n: Optional[np.ndarray]           # (rows,) int64 sample counts; clips only
    ok: Optional[np.ndarray]          # (rows,) bool; clips only
    press_row: Optional[int]          # Row of the press (an old take: its trigger), or None

    @property
    def rows(self) -> int:
        """@return Number of data rows."""
        return int(self.model.shape[0])

    @property
    def source(self) -> str:
        """@return manual, long, or take (an older snapshot-era take)."""
        if self.kind == KIND_TAKE:
            return "take"
        return self.header.get("source", "?")

# ============================================================================
# Functions
# ============================================================================


def _text(value) -> str:
    """
    @param value  A header value, or None.
    @return Its text, or NONE_TEXT for None.
    """
    return NONE_TEXT if value is None else str(value)


def clip_header(source: str, label: str, session: str, press_epoch_ms: int,
                press_n: Optional[int], press_row: Optional[int], pre_s: float, post_s: float,
                rate_hz: float, facts: ClipFacts, boot_raw: Optional[str], gui_commit: str,
                stop_epoch_ms: Optional[int] = None) -> str:
    """
    The "# key=value" header of a clip, in the handoff's order, with session,
    press_pc, press_row, rate_hz, stop_epoch_ms / stop_pc (long recordings)
    and columns added.
    @param source          cfg.SOURCE_MANUAL or cfg.SOURCE_LONG.
    @param label           Its label.
    @param session         Session name.
    @param press_epoch_ms  Laptop time of the press (a long recording: its start), ms.
    @param press_n         Last sample count received at the press, or None.
    @param press_row       Row index of that sample in the clip, or None.
    @param pre_s           Seconds kept before the press (0 for a long recording).
    @param post_s          Seconds after the press (a long recording: its length).
    @param rate_hz         Sample rate the clip was recorded at.
    @param facts           The clip's ClipFacts.
    @param boot_raw        The connection's BOOT line (or a CFG's), or None.
    @param gui_commit      Commit of this checkout.
    @param stop_epoch_ms   Laptop time a long recording stopped, or None.
    @return The header lines, each ending in a newline.
    """
    pairs = [("format", cfg.CLIP_FORMAT),
             ("source", source),
             ("label", label),
             ("session", session),
             ("press_epoch_ms", press_epoch_ms),
             ("press_pc", iso_time(press_epoch_ms / 1000.0)),
             ("press_n", _text(press_n)),
             ("press_row", _text(press_row))]
    if stop_epoch_ms is not None:
        pairs += [("stop_epoch_ms", stop_epoch_ms),
                  ("stop_pc", iso_time(stop_epoch_ms / 1000.0))]
    pairs += [("first_n", _text(facts.first_n)),
              ("last_n", _text(facts.last_n)),
              ("pre_s", "%.3f" % pre_s),
              ("post_s", "%.3f" % post_s),
              ("rate_hz", "%g" % rate_hz),
              ("rows", facts.rows),
              ("missing_samples", facts.missing),
              ("resets", facts.resets),
              ("ok0_rows", facts.ok0),
              ("boot", boot_raw if boot_raw else "missing (not seen on this connection)"),
              ("gui_commit", gui_commit),
              ("columns", ",".join(CLIP_COLUMNS))]
    return header_text(pairs)


def clip_kind(header: dict) -> Optional[str]:
    """
    @param header  A file's "# key=value" header.
    @return KIND_CLIP, KIND_TAKE, or None if it is neither.
    """
    if header.get("format") == cfg.CLIP_FORMAT:
        return KIND_CLIP
    if "take_format" in header:
        return KIND_TAKE
    return None


def boot_settings(raw: str) -> dict:
    """
    @param raw  A header's boot value: a BOOT line, or "missing (...)".
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


def _number(text: Optional[str], kind=float) -> Optional[float]:
    """
    @param text  Header text, or None.
    @param kind  float or int.
    @return The number, or None if it is missing or does not parse.
    """
    if text is None:
        return None
    try:
        return kind(text)
    except ValueError:
        return None


def _loadtxt(path: Path, usecols, ncols: int) -> np.ndarray:
    """
    Reads the data rows of a clip or take ('#' lines are the header).
    @param path     The file.
    @param usecols  Column indexes to keep, or None for all.
    @param ncols    Columns expected in the result.
    @return (rows, ncols) float64; (0, ncols) for a file with no rows.
    @raise ValueError if a row does not parse or has the wrong column count.
    """
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)   # "input contained no data"
        try:
            data = np.loadtxt(path, delimiter=",", comments="#", usecols=usecols, ndmin=2,
                              dtype=np.float64)
        except (ValueError, IndexError) as exc:        # Bad number, or too few columns
            raise ValueError("%s: a data row does not parse (%s)" % (path.name, exc))
    if data.size == 0:
        return np.zeros((0, ncols), dtype=np.float64)
    if data.shape[1] != ncols:
        raise ValueError("%s: %d columns, expected %d" % (path.name, data.shape[1], ncols))
    if not np.all(np.isfinite(data)):
        raise ValueError("%s: a value is not a finite number" % path.name)
    return data


def load_clip(path: Path) -> LoadedClip:
    """
    Reads a clip_v1 file or an older snapshot-era take. Never writes.
    @param path  The file.
    @return The LoadedClip.
    @raise ValueError if it is neither kind or a row does not parse; OSError
           if it cannot be read.
    """
    path = Path(path)
    header = read_header(path)
    kind = clip_kind(header)
    if kind is None:
        raise ValueError("%s: not a %s clip or an older take (no format header)"
                         % (path.name, cfg.CLIP_FORMAT))
    parsed = parse_take_name(path)
    label = parsed[0] if parsed is not None else header.get("label", "?")
    if kind == KIND_CLIP:
        data = _loadtxt(path, _CLIP_USECOLS, len(_CLIP_USECOLS))
        boot_odr = _number(boot_settings(header.get("boot", "")).get("odr"))
        rate = _number(header.get("rate_hz")) or boot_odr or float(cfg.DEFAULT_RATE_HZ)
        press_row = _number(header.get("press_row"), int)
        if press_row is not None and not 0 <= press_row < data.shape[0]:
            press_row = None
        return LoadedClip(path, header, kind, label, float(rate), data[:, 1:4],
                          data[:, 0].astype(np.int64), data[:, 4] != 0.0, press_row)
    data = _loadtxt(path, None, 3)
    rate = _number(header.get("rate_hz")) or float(cfg.DEFAULT_RATE_HZ)
    n_pre = _number(header.get("n_pre"), int)
    press_row = n_pre if n_pre is not None and 0 <= n_pre < data.shape[0] else None
    return LoadedClip(path, header, kind, label, float(rate), data, None, None, press_row)


def info_lines(clip: LoadedClip) -> list:
    """
    The plain facts the GUI's info panel shows for one clip. Facts only: no
    judgement, nothing hidden (O3).
    @param clip  A loaded clip or take.
    @return (name, value) text pairs, in display order.
    """
    h = clip.header
    rate = clip.rate_hz
    lines = [("File", clip.path.name), ("Label", clip.label)]
    if clip.kind == KIND_TAKE:
        lines += [("Source", "older snapshot take (trigger-aligned)"),
                  ("Rows", "%d (%.2f s)" % (clip.rows, clip.rows / rate)),
                  ("Received", h.get("pc_received") or h.get("pc_time_est", "?")),
                  ("seq", h.get("seq", "?")),
                  ("Rule class", h.get("rule_class", "?")),
                  ("Firmware", h.get("fw", "?")),
                  ("Missing samples", "not recorded in older takes")]
    else:
        long_rec = h.get("source") == cfg.SOURCE_LONG
        lines.append(("Source", "long NONE recording" if long_rec else "key press"))
        lines.append(("Started" if long_rec else "Pressed", h.get("press_pc", "?")))
        if long_rec:
            lines.append(("Stopped", h.get("stop_pc", "?")))
            lines.append(("Length", "%s s" % h.get("post_s", "?")))
        else:
            pre = _number(h.get("pre_s")) or 0.0
            post = _number(h.get("post_s")) or 0.0
            expected = int(round(pre * rate)) + int(round(post * rate))
            lines.append(("Press n", h.get("press_n", "?")))
            lines.append(("Rows", "%d (full clip: %d)" % (clip.rows, expected)))
        if long_rec:
            lines.append(("Rows", "%d" % clip.rows))
        lines += [("Missing samples", h.get("missing_samples", "?")),
                  ("Resets", h.get("resets", "?")),
                  ("ok=0 rows", h.get("ok0_rows", "?")),
                  ("n range", "%s to %s" % (h.get("first_n", "?"), h.get("last_n", "?"))),
                  ("Firmware", boot_settings(h.get("boot", "")).get("fw", "BOOT not seen"))]
    if "relabeled" in h:
        lines.append(("Relabeled", h["relabeled"]))
    return lines
