"""
File:        record_core.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.2.2
Change:      0.2.2 (owner, 2026-11-23): no manual_weak counter; every manual
             take counts in manual_takes, however weak the event.
             0.2.1: on_settings() takes the settings of a CFG line (the board
             repeats BOOT's settings) while no BOOT is known on this
             connection; nothing waiting is cleared, since a CFG is not a
             reboot. on_new_connection() forgets the previous connection's
             BOOT.
             0.2.0 (drive-log-manual-record-handoff.md v0.2, request 5;
             one-press-manual-takes-handoff.md v0.1, request 8): counters for
             the log build's manual Record outcomes and marks
             (count_manual(), count_mark()).
Description: The record logic behind the GUI, with no Qt dependency so it is
             unit-tested: arming and labels, pairing each snapshot with its
             EVT line, timeouts, resets, quick labels for car sessions, and
             the counters the GUI shows. Rules from
             cnn-record-train-handoff.md v0.4, "GUI record path changes" and
             "Matching in the GUI":
             - Arm saves the next valid snapshot, then disarms; "keep armed"
               saves every snapshot under the same label. Snapshots arriving
               while disarmed are shown but not saved.
             - A snapshot and its EVT are paired by seq, in either order. A
               snapshot whose EVT does not arrive within EVT_MATCH_TIMEOUT_S
               is saved with the rule fields marked missing. An EVT whose
               snapshot never arrives is counted, not saved.
             - seq restarts at 0 on every boot (CR-6), so on BOOT or a
               reconnect everything unmatched is cleared: snapshots already
               claimed for saving are saved at once (rule fields missing)
               rather than lost, and waiting EVTs are counted.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import cnn_config as cfg
from snapshot_protocol import BootLine, EventLine, HealthLine, Rejected, Snapshot

# ============================================================================
# Classes
# ============================================================================


@dataclass
class Claim:
    """A snapshot chosen for saving: its label and session, fixed when it
    arrived. The label can still change (quick label) until it is saved."""
    label: str
    session: str
    boot_raw: Optional[str]          # Latest BOOT line on this connection, if any
    path: Optional[Path] = None      # Set once saved


@dataclass
class Match:
    """Outcome of pairing. kind: "paired", "snap_only" or "evt_only"."""
    kind: str
    snap: Optional[Snapshot] = None
    claim: Optional[Claim] = None
    evt: Optional[EventLine] = None
    snap_t: float = 0.0              # Wall time (time.time()) the snapshot arrived
    reason: str = ""                 # Why the EVT is missing (snap_only)


class EventMatcher:
    """Pairs snapshots and EVT lines by seq, whichever arrives first."""

    def __init__(self, timeout_s: float) -> None:
        """
        @param timeout_s  How long an item waits for its partner.
        """
        self._timeout_s = timeout_s
        self._snaps = {}  # seq -> (Snapshot, Claim or None, wall time, monotonic time)
        self._evts = {}   # seq -> (EventLine, monotonic time)

    def add_snapshot(self, snap: Snapshot, claim: Optional[Claim], wall: float,
                     now: float) -> list:
        """
        @param snap   A validated snapshot.
        @param claim  Its Claim if it is to be saved, else None.
        @param wall   Wall time it arrived (time.time()).
        @param now    Monotonic time.
        @return Matches completed now.
        """
        out = []
        if snap.seq in self._evts:
            evt, _ = self._evts.pop(snap.seq)
            return [Match("paired", snap, claim, evt, wall)]
        if snap.seq in self._snaps:
            # The same seq twice without a reset: the older one cannot be
            # paired any more, so it is finished without its EVT.
            old, old_claim, old_wall, _ = self._snaps.pop(snap.seq)
            out.append(Match("snap_only", old, old_claim, None, old_wall,
                             "a newer snapshot reused its seq"))
        self._snaps[snap.seq] = (snap, claim, wall, now)
        return out

    def add_event(self, evt: EventLine, now: float) -> list:
        """
        @param evt  A parsed EVT line.
        @param now  Monotonic time.
        @return Matches completed now.
        """
        out = []
        if evt.seq in self._snaps:
            snap, claim, wall, _ = self._snaps.pop(evt.seq)
            return [Match("paired", snap, claim, evt, wall)]
        if evt.seq in self._evts:
            old, _ = self._evts.pop(evt.seq)
            out.append(Match("evt_only", evt=old))
        self._evts[evt.seq] = (evt, now)
        return out

    def expire(self, now: float) -> list:
        """
        Finishes every item that has waited longer than the timeout.
        @param now  Monotonic time.
        @return The timed-out items as snap_only / evt_only Matches.
        """
        out = []
        for seq in [s for s, v in self._snaps.items() if now - v[3] >= self._timeout_s]:
            snap, claim, wall, _ = self._snaps.pop(seq)
            out.append(Match("snap_only", snap, claim, None, wall,
                             "no EVT within %.1f s" % self._timeout_s))
        for seq in [s for s, v in self._evts.items() if now - v[1] >= self._timeout_s]:
            evt, _ = self._evts.pop(seq)
            out.append(Match("evt_only", evt=evt))
        return out

    def reset(self, reason: str) -> list:
        """
        Finishes everything waiting (on BOOT or a reconnect, when seq restarts).
        @param reason  Why, recorded with each snapshot saved without its EVT.
        @return All waiting items as snap_only / evt_only Matches.
        """
        out = [Match("snap_only", snap, claim, None, wall, reason)
               for snap, claim, wall, _ in self._snaps.values()]
        out += [Match("evt_only", evt=evt) for evt, _ in self._evts.values()]
        self._snaps.clear()
        self._evts.clear()
        return out


@dataclass
class Counters:
    """What the GUI shows. All counts are since the GUI started."""
    snapshots: int = 0          # Valid snapshots received
    saved: int = 0              # Takes written to disk
    rejected: int = 0           # Snapshots the GUI discarded (gap, CRC, no SNE, ...)
    not_armed: int = 0          # Valid snapshots shown but not saved (disarmed)
    evt_without_snap: int = 0   # EVTs whose snapshot never arrived
    saved_without_evt: int = 0  # Takes saved with the rule fields missing
    save_errors: int = 0        # Takes that could not be written
    device_snap_drops: Optional[int] = None  # snap_drops from the latest HLT (record build)
    # Log build (manual Record, request 8; marks, request 5)
    manual_takes: int = 0       # Takes saved to the session folder (any strength)
    manual_no_take: int = 0     # Raw recording kept, but no take (no movement, edge)
    manual_not_saved: int = 0   # Nothing saved (gap, reset, ok=0, lost port, write error)
    marks: int = 0              # MARK lines sent to the drive log


class RecordController:
    """Arm / label / session state plus pairing and saving. The GUI calls the
    on_* methods from the GUI thread and refreshes its display afterwards."""

    def __init__(self, store, gui_commit: str, timeout_s: float = cfg.EVT_MATCH_TIMEOUT_S,
                 on_saved: Optional[Callable[[Path, Claim], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None,
                 on_info: Optional[Callable[[str], None]] = None) -> None:
        """
        @param store       A take_store.TakeStore.
        @param gui_commit  Commit of this checkout, written into every take.
        @param timeout_s   EVT pairing timeout.
        @param on_saved    Called with (path, claim) after each take is written.
        @param on_error    Called with a message when a take cannot be written.
        @param on_info     Called with a message for the log: an EVT without its
                           snapshot, or a take saved without its EVT.
        """
        self.store = store
        self.gui_commit = gui_commit
        self.matcher = EventMatcher(timeout_s)
        self.counters = Counters()
        self.armed = False
        self.keep_armed = False
        self.label = cfg.UNLABELED      # Never mislabels by default
        self.session = ""
        self.boot: Optional[BootLine] = None     # Latest BOOT on this connection (or a
                                                 # CFG's settings, if no BOOT came)
        self.last_claim: Optional[Claim] = None  # Most recent snapshot chosen for saving
        self.last_snapshot: Optional[Snapshot] = None
        self.last_rule_class: Optional[str] = None
        self.last_reject_reason = ""
        self._on_saved = on_saved
        self._on_error = on_error
        self._on_info = on_info

    # ------------------------------------------------------------ user input

    def arm(self) -> None:
        """Arms: the next valid snapshot is saved, then the widget disarms."""
        self.armed = True

    def disarm(self) -> None:
        """Cancels a pending Arm (keep armed is separate)."""
        self.armed = False

    def quick_label(self, label: str) -> Optional[Path]:
        """
        Labels the most recent take (car sessions). If it is still waiting
        for its EVT, the label changes before it is saved; otherwise the
        saved file is relabeled.
        @param label  One of cfg.LABELS.
        @return The take's new path if it was relabeled on disk, else None.
        """
        claim = self.last_claim
        if claim is None or label not in cfg.LABELS:
            return None
        if claim.path is None:
            claim.label = label
            return None
        if label == claim.label:
            return claim.path
        try:
            claim.path = self.store.relabel(claim.path, label)
            claim.label = label
        except OSError as exc:
            self._error("Relabel failed: %s" % exc)
            return None
        return claim.path

    # ----------------------------------------------------------- device input

    def on_snapshot(self, snap: Snapshot, wall: float, now: float) -> None:
        """
        A valid snapshot arrived. It is claimed for saving if armed.
        @param snap  The snapshot.
        @param wall  Wall time (time.time()).
        @param now   Monotonic time.
        """
        self.counters.snapshots += 1
        self.last_snapshot = snap
        self.last_rule_class = None
        claim = None
        if self.armed or self.keep_armed:
            claim = Claim(self.label, self.session, self.boot.raw if self.boot else None)
            self.last_claim = claim
            if not self.keep_armed:
                self.armed = False  # Arm saves one snapshot
        else:
            self.counters.not_armed += 1
        self._handle(self.matcher.add_snapshot(snap, claim, wall, now))

    def on_event(self, evt: EventLine, now: float) -> None:
        """
        An EVT line arrived.
        @param evt  The parsed EVT.
        @param now  Monotonic time.
        """
        if self.last_snapshot is not None and evt.seq == self.last_snapshot.seq:
            self.last_rule_class = evt.fields.get("class")
        self._handle(self.matcher.add_event(evt, now))

    def on_rejected(self, rej: Rejected) -> None:
        """
        @param rej  A snapshot the parser discarded.
        """
        self.counters.rejected += 1
        self.last_reject_reason = rej.reason

    def on_boot(self, boot: BootLine) -> None:
        """
        The device (re)booted: seq restarts at 0, so nothing waiting can be
        paired any more.
        @param boot  The BOOT line.
        """
        self._handle(self.matcher.reset("device rebooted before its EVT"))
        self.boot = boot

    def on_reconnect(self) -> None:
        """
        The port came back after being lost (a reset re-enumerates native
        USB, and its BOOT line may be lost). Clears everything unmatched.
        """
        self._handle(self.matcher.reset("USB reconnected before its EVT"))
        self.boot = None

    def on_new_connection(self) -> None:
        """
        Connect was pressed: the previous connection's BOOT says nothing
        about the board now (it may have been reflashed in between).
        """
        self.boot = None

    def on_settings(self, boot: BootLine) -> bool:
        """
        The board's settings from a CFG line, as the BOOT line it repeats.
        Used only while no BOOT is known on this connection (BOOT is sent
        once, at reset, and is easily missed). Not a reboot, so nothing
        waiting is cleared.
        @param boot  ConfigLine.as_boot() of the CFG.
        @return True if these became the connection's settings.
        """
        if self.boot is not None:
            return False
        self.boot = boot
        return True

    def on_health(self, hlt: HealthLine) -> None:
        """
        @param hlt  An HLT line; snap_drops is present only in record builds.
        """
        if "snap_drops" in hlt.fields:
            self.counters.device_snap_drops = hlt.fields["snap_drops"]

    def tick(self, now: float) -> None:
        """
        Called a few times a second: finishes items that waited too long.
        @param now  Monotonic time.
        """
        self._handle(self.matcher.expire(now))

    def count_manual(self, state: str) -> None:
        """
        Counts one manual Record outcome (log build).
        @param state  manual_record.SAVED, NO_TAKE or NOT_SAVED.
        """
        if state == "saved":
            self.counters.manual_takes += 1
        elif state == "no_take":
            self.counters.manual_no_take += 1
        else:
            self.counters.manual_not_saved += 1

    def count_mark(self) -> None:
        """Counts one MARK line sent to the drive log."""
        self.counters.marks += 1

    # ---------------------------------------------------------------- helpers

    def _handle(self, matches: list) -> None:
        """
        Saves claimed snapshots and updates the counters.
        @param matches  Match items from the matcher.
        """
        for m in matches:
            if m.kind == "evt_only":
                self.counters.evt_without_snap += 1
                self._info("EVT seq %d (%s) had no valid snapshot; not saved"
                           % (m.evt.seq, m.evt.fields.get("class", "?")))
                continue
            if m.claim is None:
                continue  # Shown, not saved
            try:
                m.claim.path = self.store.save_take(
                    session=m.claim.session, label=m.claim.label, snap=m.snap,
                    evt=m.evt, boot_raw=m.claim.boot_raw, gui_commit=self.gui_commit,
                    received_wall=m.snap_t, evt_missing_reason=m.reason)
            except (OSError, ValueError) as exc:
                self.counters.save_errors += 1
                self._error("Take not saved: %s" % exc)
                continue
            self.counters.saved += 1
            if m.evt is None:
                self.counters.saved_without_evt += 1
                self._info("Take %s saved without its EVT (%s)" % (m.claim.path.name, m.reason))
            if self._on_saved is not None:
                self._on_saved(m.claim.path, m.claim)

    def _error(self, message: str) -> None:
        """
        @param message  Reported to the GUI's status line (never a popup).
        """
        if self._on_error is not None:
            self._on_error(message)

    def _info(self, message: str) -> None:
        """
        @param message  Written to the GUI log.
        """
        if self._on_info is not None:
            self._on_info(message)
