"""
File:        snapshot_plot.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.2.0
Adapted from: Python/real_time_graph.py and Python/sample_data_widget.py in
             ml-on-device-playground,
             https://github.com/jake-g/ml-on-device-playground (branch
             codegen-classroom, commit 47092aa), itself a fork of
             https://github.com/anandghanw/ml-on-device-playground.
             MIT License, Copyright (c) 2025 Anandghan; see
             LICENSE-ml-on-device-playground.txt in this folder. Kept: the
             pyqtgraph styling. Changed: display only, never a recording
             source (the framework saved one point per 20 ms plot tick); it
             shows one whole snapshot instead of a live 50 Hz trace, with the
             trigger sample marked; and g and dps get separate plots.
             Restyled (request 7): a panel; each sub-plot's name and legend
             sit in a row above it instead of a y-axis title and a legend on
             the traces, so nothing overlaps however short the plot; one left
             axis width everywhere; colors from app_styles.py.
Description: Plots one snapshot or take: z_hp (g) on top, roll and pitch
             rate (dps) below, against time from the trigger in ms. Used for
             "Last snapshot" and "Selected take". style_plot() is shared with
             the live IMU view (live_plot.py). Click a name in a legend to
             hide or show that curve.
"""

# ============================================================================
# Imports
# ============================================================================

import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QGroupBox, QVBoxLayout

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


class SnapshotPlot(QGroupBox):
    """Two linked plots of one snapshot, with the trigger at t = 0."""

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

        acc_legend = style_plot(self.p_acc, "z_hp (g)", legend_cols=1, x_values=False)
        gyr_legend = style_plot(self.p_gyr, "rate (dps)", legend_cols=2)
        trigger_pen = pg.mkPen(AppStyles.TRIGGER_LINE, width=1, style=Qt.DashLine)
        for plot in (self.p_acc, self.p_gyr):
            plot.addItem(pg.InfiniteLine(pos=0, angle=90, pen=trigger_pen))
        self.p_gyr.getAxis("bottom").setLabel("time from trigger (ms)", **axis_label_style())

        self.c0 = self.p_acc.plot(pen=channel_pen(AppStyles.PEN_Z_HP, model=True))
        self.c1 = self.p_gyr.plot(pen=channel_pen(AppStyles.PEN_ROLL, model=True))
        self.c2 = self.p_gyr.plot(pen=channel_pen(AppStyles.PEN_PITCH, model=True))
        acc_legend.addItem(self.c0, "az_hp")
        # c1 is roll and c2 pitch. Bench mapping (map_axes() in firmware-bench
        # dsp.cpp): chip Y points forward, so roll is chip gy and pitch chip gx.
        gyr_legend.addItem(self.c1, "gy (roll)")
        gyr_legend.addItem(self.c2, "gx (pitch)")

        # One line; cut off with "..." when too long, full text in the tooltip.
        self.info = set_role(ElidedLabel(""), "info")
        layout.addWidget(self.info)

    def show_values(self, values: list, n_pre: int, rate_hz: float, info: str) -> None:
        """
        Draws one snapshot.
        @param values   Rows of (z_hp, roll, pitch) floats.
        @param n_pre    Index of the trigger row.
        @param rate_hz  Sample rate, for the time axis.
        @param info     Text shown under the plots.
        """
        t = [(i - n_pre) * 1000.0 / rate_hz for i in range(len(values))]
        self.c0.setData(t, [v[0] for v in values])
        self.c1.setData(t, [v[1] for v in values])
        self.c2.setData(t, [v[2] for v in values])
        self.info.setText(info)

    def clear(self, info: str = "") -> None:
        """
        Empties the plots.
        @param info  Text shown under the plots.
        """
        for curve in (self.c0, self.c1, self.c2):
            curve.setData([], [])
        self.info.setText(info)
