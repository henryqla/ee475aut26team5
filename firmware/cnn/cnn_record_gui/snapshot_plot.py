"""
File:        snapshot_plot.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.3.0
Change:      0.3.0 (manual-recording-overhaul-handoff.md, request B step 5):
             SnapshotPlot becomes ClipPlot: one clip of any length, against
             time from the clip's start in seconds, with the key press marked
             by a vertical line (an older take's trigger the same way). Long
             recordings are drawn downsampled to the view. New: ClipInfo, the
             panel of plain facts beside the plot. The file keeps its name so
             its history stays in one place.
             0.2.0: restyled (request 7).
Adapted from: Python/real_time_graph.py and Python/sample_data_widget.py in
             ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             pyqtgraph styling. Changed: display only, never a recording
             source; one whole clip instead of a live 50 Hz trace; g and dps
             get separate plots; each sub-plot's name and legend sit in a row
             above it; colors from app_styles.py.
Description: ClipPlot draws one clip or older take: z_hp (g) on top, roll and
             pitch rate (dps) below. ClipInfo lists the clip's plain facts
             (clip_file.info_lines()) in neutral text: they never color, tag or
             judge a clip (O3). style_plot() is shared with the live IMU view
             (live_plot.py). Click a name in a legend to hide or show a curve.
"""

# ============================================================================
# Imports
# ============================================================================

import html
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QGroupBox, QLabel, QScrollArea, QVBoxLayout

from app_styles import AppStyles, channel_pen, set_role
from elided_label import ElidedLabel

# ============================================================================
# Functions
# ============================================================================


def axis_label_style() -> dict:
    """
    @return Keyword arguments for AxisItem.setLabel (white, 14 px).
    """
    return {"color": AppStyles.PLOT_TEXT, "font-size": "%dpx" % AppStyles.GRAPH_LABEL_FONT_SIZE}


def style_plot(plot: pg.PlotItem, title: str, legend_cols: int = 1,
               x_values: bool = True) -> pg.LegendItem:
    """
    Grid, white axes, a fixed-width left axis (so stacked plots line up),
    and a row above the plot with its name on the left and its legend on
    the right.
    @param plot         The plot.
    @param title        Its name with units, e.g. "accel (g)".
    @param legend_cols  Legend entries side by side (all of them: one row).
    @param x_values     Show the x tick numbers. False for the top plot of a
                        linked pair: the bottom one shows the same numbers.
    @return The legend; add each curve with legend.addItem(curve, name).
    """
    font = QFont()
    font.setPixelSize(AppStyles.GRAPH_TICK_FONT_SIZE)
    plot.showGrid(x=True, y=True, alpha=AppStyles.GRID_ALPHA)
    plot.setTitle(title, color=AppStyles.PLOT_TEXT,
                  size="%dpx" % AppStyles.GRAPH_LABEL_FONT_SIZE, justify="left")
    plot.titleLabel.setMaximumHeight(AppStyles.SUBPLOT_TITLE_HEIGHT)
    plot.layout.setRowFixedHeight(0, AppStyles.SUBPLOT_TITLE_HEIGHT)
    legend = pg.LegendItem(colCount=legend_cols, labelTextColor=AppStyles.PLOT_TEXT,
                           labelTextSize="%dpx" % AppStyles.GRAPH_TICK_FONT_SIZE)
    legend.setParentItem(plot)
    legend.anchor(itemPos=(1, 0), parentPos=(1, 0), offset=(-4, 0))  # Title row, right end
    plot.getAxis("left").setWidth(AppStyles.PLOT_AXIS_WIDTH)
    for side in ("left", "bottom"):
        plot.getAxis(side).setTextPen(AppStyles.PLOT_TEXT)
        plot.getAxis(side).setStyle(tickFont=font)
    if not x_values:
        plot.getAxis("bottom").setStyle(showValues=False)
    return legend

# ============================================================================
# Classes
# ============================================================================


class ClipPlot(QGroupBox):
    """Two linked plots of one clip, against seconds from its start."""

    def __init__(self, title: str) -> None:
        """
        @param title  Panel heading.
        """
        super().__init__(title)
        set_role(self, "plot")  # 16 px heading, as the framework's plot titles
        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)

        self.view = pg.GraphicsLayoutWidget()
        self.view.setBackground(AppStyles.PLOT_BG)
        layout.addWidget(self.view, 1)

        self.p_acc = self.view.addPlot(row=0, col=0)
        self.p_gyr = self.view.addPlot(row=1, col=0)
        self.p_gyr.setXLink(self.p_acc)  # Zoom and pan both together
        for plot in (self.p_acc, self.p_gyr):
            # Long recordings: draw only what is in view, at most one point
            # per pixel column (peaks kept), so hours of rows stay smooth.
            plot.setClipToView(True)
            plot.setDownsampling(auto=True, mode="peak")

        acc_legend = style_plot(self.p_acc, "z_hp (g)", legend_cols=1, x_values=False)
        gyr_legend = style_plot(self.p_gyr, "rate (dps)", legend_cols=2)
        press_pen = pg.mkPen(AppStyles.PRESS_LINE, width=1, style=Qt.DashLine)
        self.press_lines = []
        for plot in (self.p_acc, self.p_gyr):
            line = pg.InfiniteLine(pos=0, angle=90, pen=press_pen)
            line.hide()
            plot.addItem(line)
            self.press_lines.append(line)
        self.p_gyr.getAxis("bottom").setLabel("time from the clip's start (s)",
                                              **axis_label_style())

        self.c0 = self.p_acc.plot(pen=channel_pen(AppStyles.PEN_Z_HP, model=True))
        self.c1 = self.p_gyr.plot(pen=channel_pen(AppStyles.PEN_ROLL, model=True))
        self.c2 = self.p_gyr.plot(pen=channel_pen(AppStyles.PEN_PITCH, model=True))
        acc_legend.addItem(self.c0, "z_hp")
        # Bench mapping (map_axes() in firmware-bench dsp.cpp): chip Y points
        # forward, so roll is chip gy and pitch chip gx.
        gyr_legend.addItem(self.c1, "roll (gy)")
        gyr_legend.addItem(self.c2, "pitch (gx)")

        # One line; cut off with "..." when too long, full text in the tooltip.
        self.info = set_role(ElidedLabel(""), "info")
        layout.addWidget(self.info)

    def show_clip(self, model: np.ndarray, rate_hz: float, press_row: Optional[int],
                  info: str) -> None:
        """
        Draws one clip. The time axis is row index / rate, so it matches the
        window cutter's peak times (missing samples shorten it).
        @param model      (rows, 3) array: z_hp, roll, pitch.
        @param rate_hz    Sample rate, for the time axis.
        @param press_row  Row of the press (or an older take's trigger), or None.
        @param info       Text shown under the plots.
        """
        t = np.arange(model.shape[0], dtype=float) / float(rate_hz)
        self.c0.setData(t, model[:, 0])
        self.c1.setData(t, model[:, 1])
        self.c2.setData(t, model[:, 2])
        for line in self.press_lines:
            if press_row is None:
                line.hide()
            else:
                line.setValue(press_row / float(rate_hz))
                line.show()
        for plot in (self.p_acc, self.p_gyr):
            plot.enableAutoRange()   # A new clip: show all of it
        self.info.setText(info)

    def clear(self, info: str = "") -> None:
        """
        Empties the plots.
        @param info  Text shown under the plots.
        """
        for curve in (self.c0, self.c1, self.c2):
            curve.setData([], [])
        for line in self.press_lines:
            line.hide()
        self.info.setText(info)


class ClipInfo(QGroupBox):
    """Plain facts about the selected clip, beside the plot."""

    def __init__(self, title: str = "Clip info") -> None:
        """
        @param title  Panel heading.
        """
        super().__init__(title)
        layout = QVBoxLayout(self)
        layout.setSpacing(AppStyles.PANEL_SPACING)
        self.text = QLabel("")
        self.text.setTextFormat(Qt.RichText)
        self.text.setWordWrap(True)
        self.text.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        scroll = QScrollArea()
        scroll.setWidget(self.text)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        layout.addWidget(scroll, 1)
        self.clear("Select a clip in Collected clips.")

    def show_lines(self, lines: list) -> None:
        """
        @param lines  (name, value) text pairs (clip_file.info_lines()).
        """
        rows = "".join("<tr><td style='padding-right:10px; color:%s'>%s</td><td>%s</td></tr>"
                       % (AppStyles.TEXT_SECONDARY, html.escape(name), html.escape(str(value)))
                       for name, value in lines)
        self.text.setText("<table>%s</table>" % rows)

    def clear(self, text: str = "") -> None:
        """
        @param text  Shown instead of facts.
        """
        self.text.setText(html.escape(text))
