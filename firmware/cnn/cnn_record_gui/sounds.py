"""
File:        sounds.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.1.0
Change:      0.1.0 (manual-recording-overhaul-handoff.md, request B step 3):
             new file.
Description: The two sounds of a recording, so the passenger never has to
             look: a short tone when a press starts a clip, and a different
             two-note tone when the clip is saved. The tones are written once
             per start as small WAV files (standard library wave module) to
             this GUI's folder in the system temp folder, and played with
             QSoundEffect (QtMultimedia, part of the installed PyQt5). If
             QtMultimedia or the files are not available, QApplication.beep()
             plays instead. No new packages.
"""

# ============================================================================
# Imports
# ============================================================================

import logging
import math
import struct
import tempfile
import wave
from pathlib import Path

from PyQt5.QtCore import QObject, QUrl
from PyQt5.QtWidgets import QApplication

import cnn_config as cfg

try:  # QtMultimedia ships with the PyQt5 wheel, but a trimmed install may lack it
    from PyQt5.QtMultimedia import QSoundEffect
except ImportError:  # pragma: no cover (depends on the install)
    QSoundEffect = None

# ============================================================================
# Constants
# ============================================================================

_LOG = logging.getLogger("record")

_SAMPLE_RATE = 44100       # WAV sample rate, Hz
_FADE_S = 0.005            # Fade in and out, so a tone does not click
_GAP_S = 0.03              # Silence between the two notes of the saved tone

# ============================================================================
# Functions
# ============================================================================


def _tone_frames(freq_hz: float, length_s: float) -> bytes:
    """
    @param freq_hz   Tone frequency.
    @param length_s  Its length.
    @return 16-bit mono PCM frames of a sine at that frequency, faded in and out.
    """
    count = int(_SAMPLE_RATE * length_s)
    fade = max(1, int(_SAMPLE_RATE * _FADE_S))
    frames = bytearray()
    for i in range(count):
        gain = min(1.0, i / fade, (count - 1 - i) / fade)
        value = int(0.8 * 32767 * gain * math.sin(2 * math.pi * freq_hz * i / _SAMPLE_RATE))
        frames += struct.pack("<h", value)
    return bytes(frames)


def _write_wav(path: Path, frames: bytes) -> None:
    """
    @param path    WAV file to write.
    @param frames  16-bit mono PCM frames.
    """
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(_SAMPLE_RATE)
        w.writeframes(frames)

# ============================================================================
# Classes
# ============================================================================


class Sounds(QObject):
    """The press and saved sounds."""

    def __init__(self, parent=None) -> None:
        """
        @param parent  Qt parent (keeps the sound effects alive).
        """
        super().__init__(parent)
        self._effects = {}
        if QSoundEffect is None:
            _LOG.info("Sounds: QtMultimedia not available; using the system beep")
            return
        try:
            folder = Path(tempfile.gettempdir()) / "cnn_record_gui_sounds"
            folder.mkdir(exist_ok=True)
            press = folder / "press.wav"
            _write_wav(press, _tone_frames(cfg.SOUND_PRESS_HZ, cfg.SOUND_PRESS_MS / 1000.0))
            saved = folder / "saved.wav"
            note_s = cfg.SOUND_SAVED_MS / 1000.0
            silence = b"\x00\x00" * int(_SAMPLE_RATE * _GAP_S)
            _write_wav(saved, _tone_frames(cfg.SOUND_SAVED_HZ[0], note_s) + silence
                       + _tone_frames(cfg.SOUND_SAVED_HZ[1], note_s))
        except OSError as exc:
            _LOG.warning("Sounds: cannot write the tones (%s); using the system beep", exc)
            return
        for name, path in (("press", press), ("saved", saved)):
            effect = QSoundEffect(self)
            effect.setSource(QUrl.fromLocalFile(str(path)))
            effect.setVolume(cfg.SOUND_VOLUME)
            self._effects[name] = effect

    def press(self) -> None:
        """A press started a clip (or a long recording)."""
        self._play("press")

    def saved(self) -> None:
        """A clip was saved."""
        self._play("saved")

    def _play(self, name: str) -> None:
        """
        @param name  "press" or "saved". Falls back to the system beep if the
                     effect is missing or failed to load.
        """
        effect = self._effects.get(name)
        if effect is None or effect.status() == QSoundEffect.Error:
            QApplication.beep()
            return
        effect.play()   # Plays once loaded, if it is still loading
