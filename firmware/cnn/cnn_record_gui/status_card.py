"""
File:        status_card.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Adapted from: the "Current Classification" card in
             Python/live_inference_widget.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: a
             framed card whose border, fill and big headline change color
             with the state, a subline under it, and a row of recent
             results. Changed (record-gui-restyle-handoff.md v0.1, request
             7): it shows the latest take, or the connection and arm state,
             instead of a prediction, and adds the counters line.
Description: The "Last take" card in the record tab's left column, so the
             answer to "did that get saved?" can be read from across the
             room. Display only: the record panel tells it what happened, and
             it decides only what to show and for how long.

             What it shows, most important first:
             - a device fault (red), until clear_fault() (the next Connect);
             - a take event: saved or relabeled (green), rejected (amber),
               label pending (sky). It stays at least HOLD_MS, then gives way
               to ARMED, KEEP ARMED or NOT CONNECTED, but not to READY, so
               after a single Arm the take's name stays up;
             - the base state the record panel sets: NOT CONNECTED, READY,
               ARMED or KEEP ARMED.
"""

# ============================================================================
# Imports
# ============================================================================

from typing import Optional

from PyQt5.QtCore import QEvent, Qt, QTimer
from PyQt5.QtGui import QFont, QFontMetrics
from PyQt5.QtWidgets import QFrame, QGroupBox, QHBoxLayout, QLabel, QVBoxLayout

from app_styles import AppStyles, set_flag, set_role, set_state
from elided_label import ElidedLabel

# ============================================================================
# Constants
# ============================================================================

# Card states (style sheet "state" values).
IDLE = "idle"      # Not connected, or connected and not armed: gray
OK = "ok"          # Take saved or relabeled: green
ARMED = "armed"    # Armed, keep armed, label pending: sky
WARN = "warn"      # Snapshot rejected: amber
ERROR = "error"    # Device fault: red

# DRAFT (handoff U3). A take event stays on the card at least this long
# before an armed or not-connected state replaces it, so it can be read.
HOLD_MS = 2000

# Take names in the Recent takes row (handoff: the last five).
RECENT_MAX = 5

_TAG_MARGIN = 6    # px between the SIMULATED tag and the card's corner

# ============================================================================
# Classes
# ============================================================================


class StatusCard(QGroupBox):
    """Headline, subline, recent takes and counters."""

    def __init__(self, title: str = "Last take") -> None:
        """
        @param title  Panel heading.
        """
        super().__init__(title)
        self._base = (IDLE, "", "")
        self._base_keeps_take = False
        self._event: Optional[tuple] = None   # (state, headline, subline)
        self._fault: Optional[tuple] = None
        self._shown: Optional[tuple] = None
        self._recent = []
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._render)

        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)

        self.hero = set_role(QFrame(), "card")
        hero_layout = QVBoxLayout(self.hero)
        hero_layout.setContentsMargins(12, 8, 12, 10)
        hero_layout.setSpacing(2)
        self.headline = set_role(ElidedLabel(""), "card-headline")
        self.headline.setAlignment(Qt.AlignCenter)
        set_flag(self.headline, "fit", 0)
        self.subline = set_role(QLabel(""), "card-subline")
        self.subline.setAlignment(Qt.AlignCenter)
        self.subline.setWordWrap(True)
        hero_layout.addWidget(self.headline)
        hero_layout.addWidget(self.subline)
        layout.addWidget(self.hero)

        # Top-right corner of the card, over it (no row of its own).
        self.sim_tag = set_role(QLabel("SIMULATED", self.hero), "tag")
        set_state(self.sim_tag, WARN)
        self.sim_tag.setToolTip("The newest snapshot came from the simulated device")
        self.sim_tag.hide()
        self.hero.installEventFilter(self)

        recent_row = QHBoxLayout()
        recent_title = QLabel("Recent takes:")
        recent_title.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.recent = QLabel("")
        self.recent.setWordWrap(True)
        recent_row.addWidget(recent_title)
        recent_row.addWidget(self.recent, 1)
        layout.addLayout(recent_row)

        self.counters = set_role(QLabel(""), "info")
        self.counters.setWordWrap(True)
        layout.addWidget(self.counters)

        self._show_recent()
        self.set_base(IDLE, "NOT CONNECTED", "Pick a port or Simulated device, then Connect")

    # ------------------------------------------------------------ public API

    def set_base(self, state: str, headline: str, subline: str,
                 keeps_take: bool = False) -> None:
        """
        The connection and arm state, shown when nothing more important is.
        @param state       IDLE or ARMED.
        @param headline    E.g. READY.
        @param subline     The small text under it.
        @param keeps_take  True for READY: a take event stays up over it.
        """
        self._base = (state, headline, subline)
        self._base_keeps_take = keeps_take
        self._render()

    def show_take(self, state: str, headline: str, subline: str) -> None:
        """
        A take event (saved, relabeled, rejected, label pending).
        @param state     OK, WARN or ARMED.
        @param headline  E.g. the take's name.
        @param subline   The small text under it.
        """
        self._event = (state, headline, subline)
        self._hold.start(HOLD_MS)
        self._render()

    def show_fault(self, headline: str, subline: str) -> None:
        """
        @param headline  E.g. FAULT 3.
        @param subline   The fault text.
        """
        self._fault = (ERROR, headline, subline)
        self._render()

    def clear_fault(self) -> None:
        """A new connection: the fault no longer applies."""
        self._fault = None
        self._render()

    def set_simulated(self, simulated: bool) -> None:
        """
        @param simulated  Whether the newest snapshot came from the simulated
                          device (shows the SIMULATED tag).
        """
        if simulated == self.sim_tag.isHidden():  # isHidden: works before the window shows
            self.sim_tag.setVisible(simulated)
            self._place_tag()
            self._fit_headline()

    def add_recent(self, name: str) -> None:
        """
        @param name  A take just saved (file name without .csv).
        """
        self._recent.insert(0, name)
        del self._recent[RECENT_MAX:]
        self._show_recent()

    def rename_recent(self, old: str, new: Optional[str]) -> None:
        """
        A take was relabeled or deleted: its Recent takes entry follows, and
        so does the card if it is showing that take.
        @param old  Old take name.
        @param new  New take name, or None if deleted.
        """
        if old in self._recent:
            if new is None:
                self._recent.remove(old)
            else:
                self._recent[self._recent.index(old)] = new
            self._show_recent()
        if self._event is not None and self._event[1] == old:
            if new is None:
                self._event = (IDLE, old, "Deleted (moved to the session's _deleted folder)")
            else:
                self._event = (OK, new, "Relabeled (was %s)" % old)
            self._render()

    def clear_recent(self) -> None:
        """A new session: its takes are listed from scratch."""
        self._recent = []
        self._show_recent()

    def set_counters(self, text: str) -> None:
        """
        @param text  The counters line (may hold a line break).
        """
        self.counters.setText(text)

    # ---------------------------------------------------------------- helpers

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 (Qt name)
        """
        @param watched  The card frame.
        @param event    Its event; a resize moves the tag and refits the headline.
        @return False: the event goes on as usual.
        """
        if watched is self.hero and event.type() == QEvent.Resize:
            self._place_tag()
            self._fit_headline()
        return False

    def _render(self) -> None:
        """Shows the most important of fault, take event and base state."""
        if self._fault is not None:
            shown = self._fault
        elif self._event is not None and (self._hold.isActive() or self._base_keeps_take):
            shown = self._event
        else:
            self._event = None   # Replaced: it does not come back later
            shown = self._base
        if shown == self._shown:
            return
        self._shown = shown
        state, headline, subline = shown
        set_state(self.hero, state)
        set_state(self.headline, state)
        self.headline.setText(headline)
        self.subline.setText(subline)
        self._fit_headline()

    def _fit_headline(self) -> None:
        """Largest headline size whose text fits the card (and clears the tag)."""
        width = self.headline.contentsRect().width()
        if not self.sim_tag.isHidden():
            width -= 2 * (self.sim_tag.width() + _TAG_MARGIN)   # Centered text, tag on one side
        font = QFont(self.headline.font())
        text = self.headline.text()
        sizes = AppStyles.CARD_HEADLINE_FONT_SIZES
        fit = len(sizes) - 1
        for i, px in enumerate(sizes):
            font.setPixelSize(px)
            if QFontMetrics(font).horizontalAdvance(text) <= width:
                fit = i
                break
        set_flag(self.headline, "fit", fit)

    def _place_tag(self) -> None:
        """Keeps the SIMULATED tag in the card's top-right corner."""
        self.sim_tag.adjustSize()
        self.sim_tag.move(self.hero.width() - self.sim_tag.width() - _TAG_MARGIN, _TAG_MARGIN)
        self.sim_tag.raise_()

    def _show_recent(self) -> None:
        """Recent takes, newest first."""
        self.recent.setText(" ← ".join(self._recent) if self._recent else "none yet")
