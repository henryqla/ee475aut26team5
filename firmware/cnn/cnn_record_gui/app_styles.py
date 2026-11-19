"""
File:        app_styles.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
Adapted from: Python/app_styles.py in ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept as is,
             plus a status-text style and a combo-box style.
Description: Font sizes and style sheets shared by the record GUI's widgets.
"""

# ============================================================================
# Classes
# ============================================================================


class AppStyles:
    """Style constants and style-sheet helpers (from the framework)."""

    # Heading styles
    HEADING_FONT_SIZE = 18
    HEADING_FONT_WEIGHT = "bold"
    HEADING_STYLE = f"font-size: {HEADING_FONT_SIZE}px; font-weight: {HEADING_FONT_WEIGHT};"

    # Normal text styles
    TEXT_FONT_SIZE = 14
    TEXT_STYLE = f"font-size: {TEXT_FONT_SIZE}px;"

    # Button styles
    BUTTON_FONT_SIZE = 14
    BUTTON_STYLE = f"font-size: {BUTTON_FONT_SIZE}px;"

    # Input field styles
    INPUT_FONT_SIZE = 14
    INPUT_STYLE = f"font-size: {INPUT_FONT_SIZE}px;"

    # List item styles
    LIST_FONT_SIZE = 14
    LIST_STYLE = f"font-size: {LIST_FONT_SIZE}px;"

    # Note text styles (slightly smaller)
    NOTE_FONT_SIZE = 12
    NOTE_STYLE = f"font-size: {NOTE_FONT_SIZE}px; font-style: italic; color: gray;"

    # Status text (added): monospace so counters line up
    STATUS_STYLE = f"font-size: {TEXT_FONT_SIZE}px; font-family: Consolas, monospace;"

    # Graph title styles
    GRAPH_TITLE_FONT_SIZE = 16
    GRAPH_TITLE_STYLE = f"font-size: {GRAPH_TITLE_FONT_SIZE}px; font-weight: bold; color: white;"

    # Graph axis label styles
    GRAPH_LABEL_FONT_SIZE = 14
    GRAPH_LABEL_COLOR = "#FFFFFF"

    # Spacing between widgets
    WIDGET_SPACING = 25  # Pixels between widgets

    @staticmethod
    def get_group_box_style():
        """@return Style sheet for a QGroupBox heading."""
        return f"QGroupBox {{ {AppStyles.HEADING_STYLE} }}"

    @staticmethod
    def get_label_style():
        """@return Style sheet for a QLabel."""
        return f"QLabel {{ {AppStyles.TEXT_STYLE} }}"

    @staticmethod
    def get_button_style():
        """@return Style sheet for a QPushButton."""
        return f"QPushButton {{ {AppStyles.BUTTON_STYLE} }}"

    @staticmethod
    def get_line_edit_style():
        """@return Style sheet for a QLineEdit."""
        return f"QLineEdit {{ {AppStyles.INPUT_STYLE} }}"

    @staticmethod
    def get_combo_box_style():
        """@return Style sheet for a QComboBox (added)."""
        return f"QComboBox {{ {AppStyles.INPUT_STYLE} }}"

    @staticmethod
    def get_list_widget_style():
        """@return Style sheet for a QListWidget."""
        return f"QListWidget {{ {AppStyles.LIST_STYLE} }}"

    @staticmethod
    def get_note_label_style():
        """@return Style sheet for a note QLabel."""
        return f"QLabel {{ {AppStyles.NOTE_STYLE} }}"

    @staticmethod
    def get_status_label_style():
        """@return Style sheet for a status QLabel (added)."""
        return f"QLabel {{ {AppStyles.STATUS_STYLE} }}"
