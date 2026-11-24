"""
File:        app_styles.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.2.1
Change:      0.2.1 (manual-recording-overhaul-handoff.md, request B): the
             "record-key" button role (the large P / B / N, L and delete
             buttons) and its font size token; TRIGGER_LINE is now PRESS_LINE
             (the clip plot marks the key press, not a trigger).
Adapted from: Python/app_styles.py and Python/live_inference_widget.py in
             ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             font sizes, the 25 px spacing and the plot background. Taken
             from live_inference_widget.py: the card, accent and serial
             monitor colors. Added (record-gui-restyle-handoff.md v0.1,
             request 7): every color and size as a named token, one
             app-wide dark style sheet, a dark palette, and a dark Windows
             title bar. Values marked DRAFT fill gaps the framework leaves
             (its window colors came from macOS dark mode).
Description: The record GUI's look in one place. main.py calls apply_theme()
             once, so the app is dark on every OS whatever the computer's own
             theme. Widgets never hard-code a color or size: they take a
             "role" (set_role()) and the style sheet here says what each
             role looks like.
"""

# ============================================================================
# Imports
# ============================================================================

import sys
import tempfile
from pathlib import Path
from string import Template

import pyqtgraph as pg
from PyQt5.QtGui import QColor, QGuiApplication, QPalette, QPen
from PyQt5.QtWidgets import QApplication, QWidget

# ============================================================================
# Constants
# ============================================================================

# DwmSetWindowAttribute attributes (Windows dwmapi.h). Dark mode is 20 on
# Windows 10 20H1 and later and on Windows 11, 19 on earlier Windows 10;
# the caption color exists on Windows 11 only. Elsewhere the call fails and
# is ignored.
_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
_DWMWA_CAPTION_COLOR = 35

# Drop-down arrow (10 x 6 px triangle); %s is its fill color.
_ARROW_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' width='10' height='6' "
              "viewBox='0 0 10 6'><path d='M0 0 L10 0 L5 6 Z' fill='%s'/></svg>")

# ============================================================================
# Classes
# ============================================================================


class AppStyles:
    """Style tokens: every color, font and size the GUI uses."""

    # ---- Colors: surfaces
    WINDOW_BG = "#262626"         # DRAFT, close to the framework's dark-mode window
    PANEL_BG = "#2D2D2D"          # Framework (real_time_graph.py, card background)
    INPUT_BG = "#1E1E1E"          # DRAFT, inputs, lists and text boxes
    PANEL_BORDER = "#3F3F46"      # Framework (probability bar border)
    CARD_BORDER_IDLE = "#52525B"  # Framework (hero card)

    # ---- Colors: text
    TEXT = "#E4E4E7"              # DRAFT
    TEXT_SECONDARY = "#A1A1AA"    # Framework (card subtitle, notes)
    TEXT_DISABLED = "#71717A"     # DRAFT
    TEXT_ON_ACCENT = "#0F1E2E"    # DRAFT, dark text on a filled accent (selected tab)
    PLOT_TEXT = "#FFFFFF"         # Framework (white axes and titles)

    # ---- Colors: buttons (DRAFT, matches the framework's flat gray buttons)
    BUTTON_BG = "#3A3A3A"
    BUTTON_HOVER = "#4A4A4A"
    BUTTON_PRESSED = "#2F2F2F"
    BUTTON_BORDER = "#52525B"

    # ---- Colors: accents, each with the card fill it sits on
    ACCENT_OK = "#4ADE80"         # Saved / OK (framework: confident prediction)
    ACCENT_OK_FILL = "#072617"
    ACCENT_ARMED = "#38BDF8"      # Armed / waiting (framework: resting / Null)
    ACCENT_ARMED_FILL = "#0F1E2E"
    ACCENT_WARN = "#F59E0B"       # Rejected / warning (framework: unsure)
    ACCENT_WARN_FILL = "#271F10"
    ACCENT_ERROR = "#F87171"      # Fault / error (DRAFT; the framework has none)
    ACCENT_ERROR_FILL = "#2A1215"

    # ---- Colors: serial monitor (framework serial log)
    MONITOR_BG = "#1E222A"
    MONITOR_TEXT = "#D8DEE9"

    # ---- Colors: plots
    PLOT_BG = PANEL_BG
    GRID_ALPHA = 0.3              # Framework
    # Model channels (framework red / green / blue pens, matched to the
    # channel colors the GUI already used): same color in every plot.
    PEN_Z_HP = "#00FF00"
    PEN_ROLL = "#0000FF"          # Roll: chip gy (bench mapping, chip Y forward)
    PEN_PITCH = "#FF0000"         # Pitch: chip gx
    MODEL_PEN_WIDTH = 2
    # Raw channels, live plot only (DRAFT): muted, so the model channels stand out.
    PEN_AX = "#B8875A"
    PEN_AY = "#A67BA6"
    PEN_AZ = "#5FA8A8"
    PEN_GZ = "#B5AE62"
    RAW_PEN_WIDTH = 1
    PRESS_LINE = ACCENT_WARN      # Dashed line at the key press (or an old take's trigger)

    # ---- Fonts
    MONO_FONT = "Consolas, Menlo, Monaco, monospace"  # Framework list plus Consolas
    HEADING_FONT_SIZE = 18        # Panel headings (framework)
    TEXT_FONT_SIZE = 14           # Body text, buttons, inputs, lists (framework)
    NOTE_FONT_SIZE = 12           # Italic notes (framework)
    STRIP_FONT_SIZE = 12          # Device info strip, counters, plot info lines
    READOUT_FONT_SIZE = 13        # Live readout line
    MONITOR_FONT_SIZE = 12        # Serial monitor (framework 11)
    GRAPH_TITLE_FONT_SIZE = 16    # Plot titles (framework)
    GRAPH_LABEL_FONT_SIZE = 14    # Plot axis labels (framework)
    GRAPH_TICK_FONT_SIZE = 12     # DRAFT
    # Card headline: 36 px (framework hero card), stepping down only when a
    # long take name would not fit the card's width (DRAFT steps).
    CARD_HEADLINE_FONT_SIZES = (36, 30, 24, 20)
    CARD_SUBLINE_FONT_SIZE = 13   # Framework hero card
    TAG_FONT_SIZE = 11            # Small outlined tags (SIMULATED, live plot state)
    RECORD_KEY_FONT_SIZE = 17     # DRAFT. The large P / B / N, L and delete buttons

    # ---- Sizes and spacing (px)
    WIDGET_SPACING = 25           # Between panels (framework)
    PANEL_SPACING = 10            # Inside a panel
    PANEL_RADIUS = 6
    PANEL_TITLE_SPACE = 30        # Room above a panel's frame for its heading
    CARD_BORDER_WIDTH = 2
    CARD_RADIUS = 6
    BUTTON_RADIUS = 4
    SCROLLBAR_WIDTH = 12
    PLOT_AXIS_WIDTH = 52          # Left axis of every plot, so sub-plots line up
    SUBPLOT_TITLE_HEIGHT = 22     # Row above each sub-plot: its name and its legend

    # ---- Window layout (px)
    WINDOW_START_WIDTH = 1400
    WINDOW_START_HEIGHT = 900
    LEFT_COLUMN_MIN_WIDTH = 380   # Narrowest the left column may be dragged
    PLOT_MIN_HEIGHT = 160         # A dragged splitter never hides a plot
    MONITOR_START_HEIGHT = 150    # Serial monitor text box at start (handoff) ...
    MONITOR_START_SHARE = 0.15    # ... or this share of the right column, if less
    MONITOR_MIN_HEIGHT = 90
    LIVE_PLOT_SHARE = 0.55        # Live IMU's share of the plot height at start
    NOTE_SPACING = 4              # Between notes that follow each other
    TAKE_LIST_MIN_HEIGHT = 120    # About four takes


# ============================================================================
# Functions
# ============================================================================

# One style sheet for the whole app. $NAME is an AppStyles token; braces are
# style-sheet syntax. A widget opts into a variant with set_role().
_STYLE_SHEET = Template("""
QWidget {
    color: $TEXT;
    font-size: ${TEXT_FONT_SIZE}px;
    font-weight: normal;
}
QMainWindow, QDialog, QMessageBox {
    background-color: $WINDOW_BG;
}
QToolTip {
    background-color: $INPUT_BG;
    color: $TEXT;
    border: 1px solid $PANEL_BORDER;
    padding: 4px;
}

/* Panels: the heading sits above the frame, as in the framework. */
QGroupBox {
    background-color: $PANEL_BG;
    border: 1px solid $PANEL_BORDER;
    border-radius: ${PANEL_RADIUS}px;
    margin-top: ${PANEL_TITLE_SPACE}px;
    padding-top: 4px;
    font-size: ${HEADING_FONT_SIZE}px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 2px;
    padding: 0px 2px;
}
QGroupBox[role="plot"] {
    font-size: ${GRAPH_TITLE_FONT_SIZE}px;
}

/* Buttons */
QPushButton {
    background-color: $BUTTON_BG;
    border: 1px solid $BUTTON_BORDER;
    border-radius: ${BUTTON_RADIUS}px;
    padding: 5px 14px;
}
QPushButton:hover {
    background-color: $BUTTON_HOVER;
}
QPushButton:pressed {
    background-color: $BUTTON_PRESSED;
}
QPushButton:disabled {
    background-color: $BUTTON_PRESSED;
    border-color: $PANEL_BORDER;
    color: $TEXT_DISABLED;
}
QPushButton[armed="true"] {
    border: 2px solid $ACCENT_ARMED;
}
QPushButton[role="record-key"] {
    font-size: ${RECORD_KEY_FONT_SIZE}px;
    font-weight: bold;
    padding: 12px 8px;
}

/* Inputs */
QLineEdit, QComboBox {
    background-color: $INPUT_BG;
    border: 1px solid $BUTTON_BORDER;
    border-radius: ${BUTTON_RADIUS}px;
    padding: 4px 8px;
    selection-background-color: $ACCENT_ARMED;
    selection-color: $TEXT_ON_ACCENT;
}
QComboBox {
    combobox-popup: 0;  /* List popup (as Windows draws it), so the colors below apply */
}
QLineEdit:focus, QComboBox:focus {
    border-color: $ACCENT_ARMED;
}
QLineEdit:disabled, QComboBox:disabled {
    color: $TEXT_DISABLED;
    border-color: $PANEL_BORDER;
}
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 22px;
    border: none;
}
QComboBox::down-arrow {
    image: url("$ARROW");
    width: 10px;
    height: 6px;
}
QComboBox::down-arrow:disabled {
    image: url("$ARROW_DISABLED");
}
QComboBox QAbstractItemView {
    background-color: $INPUT_BG;
    border: 1px solid $PANEL_BORDER;
    outline: 0px;
    selection-background-color: $ACCENT_ARMED;
    selection-color: $TEXT_ON_ACCENT;
}

/* Lists */
QListWidget {
    background-color: $INPUT_BG;
    border: 1px solid $PANEL_BORDER;
    border-radius: ${BUTTON_RADIUS}px;
    outline: 0px;
}
QListWidget::item {
    padding: 3px 6px;
}
QListWidget::item:hover {
    background-color: $PANEL_BG;
}
QListWidget::item:selected {
    background-color: $BUTTON_BG;
    color: $TEXT;
}

/* Check boxes: Fusion draws the box and tick from the dark palette. */
QCheckBox {
    spacing: 8px;
}
QCheckBox:disabled {
    color: $TEXT_DISABLED;
}

/* Tabs: centered, the selected one filled with the sky accent. */
QTabWidget::pane {
    border: none;
}
QTabWidget::tab-bar {
    alignment: center;
}
QTabBar::tab {
    margin-top: 6px;
    background-color: $BUTTON_BG;
    color: $TEXT_SECONDARY;
    border: 1px solid $BUTTON_BORDER;
    padding: 5px 22px;
    min-width: 70px;
}
QTabBar::tab:first {
    border-top-left-radius: ${BUTTON_RADIUS}px;
    border-bottom-left-radius: ${BUTTON_RADIUS}px;
}
QTabBar::tab:last {
    border-top-right-radius: ${BUTTON_RADIUS}px;
    border-bottom-right-radius: ${BUTTON_RADIUS}px;
}
QTabBar::tab:hover:!selected {
    background-color: $BUTTON_HOVER;
}
QTabBar::tab:selected {
    background-color: $ACCENT_ARMED;
    border-color: $ACCENT_ARMED;
    color: $TEXT_ON_ACCENT;
    font-weight: bold;
}

/* Splitter bars are the gaps between panels; they light up under the mouse. */
QSplitter::handle {
    background-color: transparent;
}
QSplitter::handle:hover {
    background-color: $BUTTON_PRESSED;
}

/* Scroll bars */
QScrollBar:vertical {
    background-color: $INPUT_BG;
    width: ${SCROLLBAR_WIDTH}px;
    margin: 0px;
}
QScrollBar:horizontal {
    background-color: $INPUT_BG;
    height: ${SCROLLBAR_WIDTH}px;
    margin: 0px;
}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
    background-color: $BUTTON_HOVER;
    border-radius: 4px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    min-height: 24px;
}
QScrollBar::handle:horizontal {
    min-width: 24px;
}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {
    background-color: $TEXT_DISABLED;
}
QScrollBar::add-line, QScrollBar::sub-line {
    width: 0px;
    height: 0px;
}
QScrollBar::add-page, QScrollBar::sub-page {
    background: none;
}

/* The left column scrolls when the window is short; no frame or fill. */
QScrollArea[role="column"], QWidget[role="column"] {
    background-color: transparent;
    border: none;
}
QScrollArea[role="column"] > QWidget#qt_scrollarea_viewport {
    background-color: transparent;
}

/* Text roles */
QLabel[role="heading"] {
    font-size: ${HEADING_FONT_SIZE}px;
    font-weight: bold;
}
QLabel[role="note"] {
    font-size: ${NOTE_FONT_SIZE}px;
    font-style: italic;
    color: $TEXT_SECONDARY;
}
QLabel[role="strip"] {
    font-size: ${STRIP_FONT_SIZE}px;
    color: $TEXT_SECONDARY;
}
QLabel[role="mono"] {
    font-family: $MONO_FONT;
}
QLabel[role="info"] {
    font-family: $MONO_FONT;
    font-size: ${STRIP_FONT_SIZE}px;
    color: $TEXT_SECONDARY;
}
QLabel[role="readout"] {
    font-family: $MONO_FONT;
    font-size: ${READOUT_FONT_SIZE}px;
}
QLabel[role="error"] {
    color: $ACCENT_ERROR;
}

/* Status card (Last take): border, fill and headline follow the state. */
QFrame[role="card"] {
    background-color: $PANEL_BG;
    border: ${CARD_BORDER_WIDTH}px solid $CARD_BORDER_IDLE;
    border-radius: ${CARD_RADIUS}px;
}
QFrame[role="card"][state="ok"] {
    background-color: $ACCENT_OK_FILL;
    border-color: $ACCENT_OK;
}
QFrame[role="card"][state="armed"] {
    background-color: $ACCENT_ARMED_FILL;
    border-color: $ACCENT_ARMED;
}
QFrame[role="card"][state="warn"] {
    background-color: $ACCENT_WARN_FILL;
    border-color: $ACCENT_WARN;
}
QFrame[role="card"][state="error"] {
    background-color: $ACCENT_ERROR_FILL;
    border-color: $ACCENT_ERROR;
}
QLabel[role="card-headline"] {
    font-weight: bold;
    color: $TEXT_SECONDARY;
}
QLabel[role="card-headline"][state="ok"] {
    color: $ACCENT_OK;
}
QLabel[role="card-headline"][state="armed"] {
    color: $ACCENT_ARMED;
}
QLabel[role="card-headline"][state="warn"] {
    color: $ACCENT_WARN;
}
QLabel[role="card-headline"][state="error"] {
    color: $ACCENT_ERROR;
}
QLabel[role="card-subline"] {
    font-size: ${CARD_SUBLINE_FONT_SIZE}px;
    color: $TEXT_SECONDARY;
}

/* Small outlined tags: SIMULATED on the card, the live plot's state. */
QLabel[role="tag"] {
    font-size: ${TAG_FONT_SIZE}px;
    font-weight: bold;
    color: $TEXT_SECONDARY;
    background-color: transparent;
    border: 1px solid $CARD_BORDER_IDLE;
    border-radius: 3px;
    padding: 1px 6px;
}
QLabel[role="tag"][state="ok"] {
    color: $ACCENT_OK;
    border-color: $ACCENT_OK;
}
QLabel[role="tag"][state="armed"] {
    color: $ACCENT_ARMED;
    border-color: $ACCENT_ARMED;
}
QLabel[role="tag"][state="warn"] {
    color: $ACCENT_WARN;
    border-color: $ACCENT_WARN;
}
QLabel[role="tag"][state="error"] {
    color: $ACCENT_ERROR;
    border-color: $ACCENT_ERROR;
}

/* Connection dot beside the connection message. */
QLabel[role="dot"] {
    color: $TEXT_DISABLED;
}
QLabel[role="dot"][state="ok"] {
    color: $ACCENT_OK;
}
QLabel[role="dot"][state="warn"] {
    color: $ACCENT_WARN;
}
QLabel[role="dot"][state="error"] {
    color: $ACCENT_ERROR;
}

/* Serial monitor */
QPlainTextEdit[role="monitor"] {
    background-color: $MONITOR_BG;
    color: $MONITOR_TEXT;
    font-family: $MONO_FONT;
    font-size: ${MONITOR_FONT_SIZE}px;
    border: 1px solid $PANEL_BORDER;
    border-radius: ${BUTTON_RADIUS}px;
    padding: 4px;
}
""")


def _tokens() -> dict:
    """
    @return Every AppStyles token by name, as text for the style sheet.
    """
    return {k: str(v) for k, v in vars(AppStyles).items() if k.isupper()}


def _arrow_images() -> dict:
    """
    Writes the drop-down arrows as small SVG files (a style sheet can only
    take an image from a file), in the token colors, to this GUI's folder in
    the system temp folder. Rewritten at every start, so they follow the
    tokens.
    @return {"ARROW": path, "ARROW_DISABLED": path}, forward slashes.
    """
    folder = Path(tempfile.gettempdir()) / "cnn_record_gui_theme"
    folder.mkdir(exist_ok=True)
    paths = {}
    for key, color in (("ARROW", AppStyles.TEXT_SECONDARY),
                       ("ARROW_DISABLED", AppStyles.TEXT_DISABLED)):
        path = folder / ("%s.svg" % key.lower())
        path.write_text(_ARROW_SVG % color, encoding="utf-8")
        paths[key] = path.as_posix()
    return paths


def build_style_sheet() -> str:
    """
    @return The app-wide style sheet, filled in from the tokens.
    """
    # The card headline's size steps, one rule each (fit="0" is the largest).
    fit = "".join('QLabel[role="card-headline"][fit="%d"] { font-size: %dpx; }\n' % (i, px)
                  for i, px in enumerate(AppStyles.CARD_HEADLINE_FONT_SIZES))
    return _STYLE_SHEET.substitute(_tokens(), **_arrow_images()) + fit


def dark_palette() -> QPalette:
    """
    The palette Fusion draws with (check boxes, focus frames, anything the
    style sheet leaves to the style), so nothing comes out light.
    @return A dark QPalette built from the tokens.
    """
    s = AppStyles
    pal = QPalette()
    roles = ((QPalette.Window, s.WINDOW_BG), (QPalette.WindowText, s.TEXT),
             (QPalette.Base, s.INPUT_BG), (QPalette.AlternateBase, s.PANEL_BG),
             (QPalette.ToolTipBase, s.INPUT_BG), (QPalette.ToolTipText, s.TEXT),
             (QPalette.PlaceholderText, s.TEXT_DISABLED), (QPalette.Text, s.TEXT),
             (QPalette.Button, s.BUTTON_BG), (QPalette.ButtonText, s.TEXT),
             (QPalette.BrightText, s.ACCENT_ERROR), (QPalette.Highlight, s.ACCENT_ARMED),
             (QPalette.HighlightedText, s.TEXT_ON_ACCENT), (QPalette.Link, s.ACCENT_ARMED),
             (QPalette.Light, s.BUTTON_HOVER), (QPalette.Midlight, s.BUTTON_BG),
             (QPalette.Mid, s.PANEL_BORDER), (QPalette.Dark, s.INPUT_BG),
             (QPalette.Shadow, s.INPUT_BG))
    for role, color in roles:
        pal.setColor(role, QColor(color))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        pal.setColor(QPalette.Disabled, role, QColor(s.TEXT_DISABLED))
    return pal


def apply_theme(app: QApplication) -> None:
    """
    Makes the whole app dark: Fusion style, the dark palette, the style
    sheet, and pyqtgraph's default colors. Call once, right after creating
    the QApplication and before any window or plot.
    @param app  The application.
    """
    app.setStyle("Fusion")
    app.setPalette(dark_palette())
    app.setStyleSheet(build_style_sheet())
    pg.setConfigOptions(background=AppStyles.PLOT_BG, foreground=AppStyles.PLOT_TEXT)


def apply_dark_title_bar(window: QWidget) -> None:
    """
    Asks Windows to draw the window's title bar dark (Windows 10 20H1 and
    later, Windows 11), in the window color where Windows 11 allows it. Does
    nothing on other systems, or if Windows refuses.
    @param window  A top-level window.
    """
    if sys.platform != "win32" or QGuiApplication.platformName() != "windows":
        return
    try:
        import ctypes
        dwm = ctypes.windll.dwmapi
        hwnd = ctypes.c_void_p(int(window.winId()))
        on = ctypes.c_int(1)
        for attr in (_DWMWA_USE_IMMERSIVE_DARK_MODE, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD):
            if dwm.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(on), ctypes.sizeof(on)) == 0:
                break
        c = QColor(AppStyles.WINDOW_BG)
        colorref = ctypes.c_int(c.red() | (c.green() << 8) | (c.blue() << 16))  # 0x00BBGGRR
        dwm.DwmSetWindowAttribute(hwnd, _DWMWA_CAPTION_COLOR, ctypes.byref(colorref),
                                  ctypes.sizeof(colorref))
    except (AttributeError, OSError, ValueError):
        pass  # Cosmetic only: a light title bar is fine


def set_role(widget: QWidget, role: str) -> QWidget:
    """
    Gives a widget a style role from the style sheet ("note", "strip",
    "mono", "info", "readout", "error", "plot", "column", "card", ...).
    @param widget  The widget.
    @param role    The role name.
    @return The same widget, so the call can wrap a constructor.
    """
    widget.setProperty("role", role)
    return widget


def set_flag(widget: QWidget, name: str, value) -> None:
    """
    Sets a style property ("state", "armed", "fit", ...) and makes the style
    sheet look at the widget again. Does nothing if the value is unchanged.
    @param widget  The widget.
    @param name    Property name.
    @param value   New value (str, bool or int).
    """
    if widget.property(name) == value:
        return
    widget.setProperty(name, value)
    widget.style().unpolish(widget)
    widget.style().polish(widget)


def set_state(widget: QWidget, state: str) -> None:
    """
    @param widget  The widget.
    @param state   Its accent: idle, ok, armed, warn or error.
    """
    set_flag(widget, "state", state)


def channel_pen(color: str, model: bool) -> QPen:
    """
    @param color  A PEN_* token.
    @param model  True for a model channel (z_hp, roll, pitch: thick), False
                  for a raw one (thin).
    @return The pen, the same for a channel in every plot.
    """
    width = AppStyles.MODEL_PEN_WIDTH if model else AppStyles.RAW_PEN_WIDTH
    return pg.mkPen(color, width=width)
