"""
File:        elided_label.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: A one-line label that never widens its column: text too long
             for its width is cut off with "..." and shown in full in its
             tooltip (record-gui-restyle-handoff.md v0.1, request 7). text()
             returns the full text, so code that reads the text back and adds
             to it (the snapshot info line) works as with a plain QLabel.
"""

# ============================================================================
# Imports
# ============================================================================

from PyQt5.QtCore import QEvent, Qt
from PyQt5.QtWidgets import QLabel, QSizePolicy

# ============================================================================
# Classes
# ============================================================================


class ElidedLabel(QLabel):
    """QLabel that elides on the right and keeps its full text."""

    def __init__(self, text: str = "") -> None:
        """
        @param text  Starting text.
        """
        super().__init__()
        self._full = ""
        self._detail = ""
        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.setText(text)

    def setText(self, text: str) -> None:  # noqa: N802 (Qt name)
        """
        @param text  The full text (line breaks are shown as spaces).
        """
        self._full = text
        self._elide()

    def text(self) -> str:
        """
        @return The full text, not the shortened one on screen.
        """
        return self._full

    def set_detail(self, detail: str) -> None:
        """
        @param detail  Tooltip text shown instead of the full text ("" for
                       the default: the full text, when it is cut off).
        """
        self._detail = detail
        self._elide()

    def resizeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        """
        @param event  The resize event: the text is shortened again.
        """
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event) -> None:  # noqa: N802 (Qt name)
        """
        @param event  A change; a new font (style sheet) changes the fit.
        """
        super().changeEvent(event)
        if event.type() == QEvent.FontChange:
            self._elide()

    def _elide(self) -> None:
        """Shows as much of the text as fits, and sets the tooltip."""
        one_line = self._full.replace("\n", "  ")
        shown = self.fontMetrics().elidedText(one_line, Qt.ElideRight,
                                              max(0, self.contentsRect().width()))
        super().setText(shown)
        if self._detail:
            self.setToolTip(self._detail)
        else:
            self.setToolTip(self._full if shown != one_line else "")
