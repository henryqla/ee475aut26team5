"""
File:        record_core.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B): manual
             clips only. Removed with the snapshots: arming, keep armed, the
             EVT matcher, quick labels and the snapshot / manual / mark
             counters (git tag pre-manual-overhaul). Kept: the connection's
             settings, from BOOT or (only while no BOOT is known) from a CFG
             line. New: save_clip() writes a finished clip, delete_last()
             moves the last clip saved in this run to _deleted (Backspace),
             and session_counts() counts the session's clips from disk.
             0.2.1: on_settings() (CFG), on_new_connection().
Description: The record logic behind the GUI, with no Qt dependency so it is
             unit-tested: the session, the board's settings for clip headers,
             saving clips, the last saved clip, this run's counters, and the
             session's clip counts for the top bar. Every keep, relabel or
             delete is the owner's action (O3); nothing here decides one.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import cnn_config as cfg
from clip_file import KIND_CLIP, clip_header, clip_kind
from clip_recorder import Clip
from snapshot_protocol import BootLine
from take_store import parse_take_name, read_header

# ============================================================================
# Classes
# ============================================================================


@dataclass
class Counters:
    """This run's counts (since the GUI started)."""
    saved: int = 0          # Clips written
    save_errors: int = 0    # Clips that could not be written (disk errors only)
    ignored: int = 0        # Presses ignored (O7: during a clip; B3: during a long recording)
    deleted: int = 0        # Clips moved to _deleted with Backspace


@dataclass
class SessionCounts:
    """One session's clips on disk, for the top bar."""
    per_label: dict = field(default_factory=dict)   # Label -> clips (not long NONE)
    long_count: int = 0                              # Long NONE recordings
    long_s: float = 0.0                              # Their total length, s
    older: int = 0                                   # Older snapshot-era takes

    def text(self, session: str) -> str:
        """
        @param session  Session name.
        @return One line: the session, clips per label, long NONE minutes,
                and older takes if there are any.
        """
        parts = [session or "no session"]
        parts += ["%s %d" % (label, self.per_label.get(label, 0)) for label in cfg.LABELS]
        parts.append("long NONE %d (%.1f min)" % (self.long_count, self.long_s / 60.0))
        if self.older:
            parts.append("older takes %d" % self.older)
        return "   ·   ".join(parts)


class RecordController:
    """Session, settings and saving. The GUI calls it from the GUI thread."""

    def __init__(self, store, gui_commit: str,
                 on_saved: Optional[Callable[[Path, Clip], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None) -> None:
        """
        @param store       A take_store.TakeStore.
        @param gui_commit  Commit of this checkout, written into every clip.
        @param on_saved    Called with (path, clip) after each clip is written.
        @param on_error    Called with a message when a clip cannot be written
                           or deleted.
        """
        self.store = store
        self.gui_commit = gui_commit
        self.counters = Counters()
        self.session = ""
        self.boot: Optional[BootLine] = None     # Latest BOOT on this connection (or a
                                                 # CFG's settings, if no BOOT came)
        self.last_saved: Optional[Path] = None   # Last clip saved in this run (Backspace)
        self._on_saved = on_saved
        self._on_error = on_error

    # ----------------------------------------------------------- board settings

    def on_boot(self, boot: BootLine) -> None:
        """
        @param boot  A BOOT line: the board (re)booted; these are its settings.
        """
        self.boot = boot

    def on_reconnect(self) -> None:
        """The port came back after being lost (a reset re-enumerates native
        USB, and its BOOT line may be lost): the old settings may not apply."""
        self.boot = None

    def on_new_connection(self) -> None:
        """Connect was pressed: the previous connection's BOOT says nothing
        about the board now (it may have been reflashed in between)."""
        self.boot = None

    def on_settings(self, boot: BootLine) -> bool:
        """
        The board's settings from a CFG line, as the BOOT line it repeats.
        Used only while no BOOT is known on this connection (BOOT is sent
        once, at reset, and is easily missed).
        @param boot  ConfigLine.as_boot() of the CFG.
        @return True if these became the connection's settings.
        """
        if self.boot is not None:
            return False
        self.boot = boot
        return True

    # ----------------------------------------------------------------- clips

    def save_clip(self, clip: Clip) -> Optional[Path]:
        """
        Writes a finished clip to the session folder, whatever its facts (O3).
        A long recording is copied from its rows file, which is then removed.
        The header takes the connection's settings as they are now.
        @param clip  From the ClipRecorder.
        @return The new file, or None if it could not be written (reported
                through on_error; a long recording's rows file is then kept).
        """
        head = clip_header(source=clip.source, label=clip.label, session=self.session,
                           press_epoch_ms=clip.press_epoch_ms, press_n=clip.press_n,
                           press_row=clip.press_row, pre_s=clip.pre_s, post_s=clip.post_s,
                           rate_hz=clip.rate_hz, facts=clip.facts,
                           boot_raw=self.boot.raw if self.boot is not None else None,
                           gui_commit=self.gui_commit, stop_epoch_ms=clip.stop_epoch_ms)
        try:
            if clip.body_path is not None:
                path = self.store.write_take_streamed(self.session, clip.label, head,
                                                      clip.body_path)
            else:
                path = self.store.write_take(self.session, clip.label, head + clip.rows_text())
        except (OSError, ValueError) as exc:
            self.counters.save_errors += 1
            kept = " (its rows are kept in %s)" % clip.body_path if clip.body_path else ""
            self._error("Clip not saved: %s%s" % (exc, kept))
            return None
        if clip.body_path is not None:
            try:
                os.remove(clip.body_path)
            except OSError as exc:
                self._error("Clip %s saved, but its rows file %s could not be removed: %s"
                            % (path.name, clip.body_path.name, exc))
        self.counters.saved += 1
        self.last_saved = path
        if self._on_saved is not None:
            self._on_saved(path, clip)
        return path

    def delete_last(self) -> Optional[tuple]:
        """
        Backspace: moves the last clip saved in this run to _deleted. Once
        moved, Backspace does nothing until the next clip is saved.
        @return (old path, new path), or None if there is none (or the move
                failed, reported through on_error).
        """
        path = self.last_saved
        if path is None:
            return None
        try:
            dest = self.store.delete(path)
        except OSError as exc:
            self._error("Delete failed: %s" % exc)
            return None
        self.last_saved = None
        self.counters.deleted += 1
        return path, dest

    def take_moved(self, old: Path, new: Optional[Path]) -> None:
        """
        The clip list relabeled or deleted a clip: keeps last_saved right.
        @param old  Old path.
        @param new  New path, or None if deleted.
        """
        if self.last_saved is not None and self.last_saved == old:
            self.last_saved = new

    def session_counts(self, session: Optional[str] = None) -> SessionCounts:
        """
        Counts a session's clips from disk, so relabels and deletes show.
        @param session  Session name (default: the current one).
        @return The counts (empty if the session has no folder yet).
        """
        session = self.session if session is None else session
        counts = SessionCounts()
        if not session:
            return counts
        try:
            paths = self.store.list_takes(session)
        except (OSError, ValueError):
            return counts
        for path in paths:
            label = parse_take_name(path)[0]
            try:
                header = read_header(path)
            except OSError:
                continue
            if clip_kind(header) != KIND_CLIP:
                counts.older += 1
            elif header.get("source") == cfg.SOURCE_LONG and label == cfg.LONG_LABEL:
                counts.long_count += 1
                try:
                    counts.long_s += float(header.get("post_s", "0"))
                except ValueError:
                    pass
            else:
                counts.per_label[label] = counts.per_label.get(label, 0) + 1
        return counts

    # ---------------------------------------------------------------- helpers

    def _error(self, message: str) -> None:
        """
        @param message  Reported to the GUI's status line (never a popup).
        """
        if self._on_error is not None:
            self._on_error(message)
