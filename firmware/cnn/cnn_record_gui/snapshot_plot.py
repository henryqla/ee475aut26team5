"""
File:        snapshot_plot.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-18
Version:     0.1.0
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
Description: Plots one snapshot or take: z_hp (g) on top, roll and pitch
             rate (dps) below, against time from the trigger in ms. Used for
             "Last snapshot" and "Selected take". style_plot() is shared with
             the live IMU view (live_plot.py).
"""

# ============================================================================
# Imports
# ============================================================================

import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget

from app_styles import AppStyles

# ============================================================================
# Constants
# ============================================================================

PLOT_BACKGROUND = "#2D2D2D"  # Dark grey, as in the framework

# ============================================================================
# Functions
# ============================================================================


def axis_label_style() -> dict:
    """
    @return Keyword arguments for AxisItem.setLabel (white, framework size).
    """
    return {"color": AppStyles.GRAPH_LABEL_COLOR,
            "font-size": "%dpt" % (AppStyles.GRAPH_LABEL_FONT_SIZE - 4)}


def style_plot(plot: pg.PlotItem, ylabel: str, legend_cols: int = 1,
               x_values: bool = True) -> None:
    """
    Grid, legend, white axes and the y label, as in the framework. Call
    before adding curves, so they join the legend.
    @param plot         The plot.
    @param ylabel       Y axis label, units included in the text (pyqtgraph's
                        units= would add SI prefixes: "mg", "kdps").
    @param legend_cols  Legend entries side by side (more = a shorter legend).
    @param x_values     Show the x tick numbers. False for the top plot of a
                        linked pair: the bottom one shows the same numbers,
                        and the plots get the height back.
    """
    font = QFont()
    font.setPointSize(AppStyles.TEXT_FONT_SIZE - 4)
    plot.showGrid(x=True, y=True, alpha=0.3)
    # In the top-left corner: pyqtgraph's default offset (30, 30) puts the
    # legend below the bottom of a short plot, where it cannot be seen.
    plot.addLegend(offset=(2, 2), labelTextColor="w", colCount=legend_cols)
    plot.getAxis("left").setLabel(ylabel, **axis_label_style())
    for side in ("left", "bottom"):
        plot.getAxis(side).setTextPen("w")
        plot.getAxis(side).setStyle(tickFont=font)
    if not x_values:
        plot.getAxis("bottom").setStyle(showValues=False)

# ============================================================================
# Classes
# ============================================================================


class SnapshotPlot(QWidget):
    """Two linked plots of one snapshot, with the trigger at t = 0."""

    def __init__(self, title: str) -> None:
        """
        @param title  Heading above the plots.
        """
        super().__init__()
        layout = QVBoxLayout(self)

        self.title = QLabel(title)
        self.title.setStyleSheet(AppStyles.HEADING_STYLE)
        layout.addWidget(self.title)

        self.view = pg.GraphicsLayoutWidget()
        self.view.setBackground(PLOT_BACKGROUND)
        layout.addWidget(self.view, 1)

        self.p_acc = self.view.addPlot(row=0, col=0)
        self.p_gyr = self.view.addPlot(row=1, col=0)
        self.p_gyr.setXLink(self.p_acc)  # Zoom and pan both together

        for plot, ylabel, top, cols in ((self.p_acc, "z_hp (g)", True, 1),
                                        (self.p_gyr, "rate (dps)", False, 2)):
            style_plot(plot, ylabel, legend_cols=cols, x_values=not top)
            plot.addItem(pg.InfiniteLine(pos=0, angle=90,
                                         pen=pg.mkPen("y", width=1, style=Qt.DashLine)))
        self.p_gyr.getAxis("bottom").setLabel("time from trigger (ms)", **axis_label_style())

        self.c0 = self.p_acc.plot(pen=pg.mkPen("g", width=2), name="az_hp")
        self.c1 = self.p_gyr.plot(pen=pg.mkPen("b", width=2), name="gx (roll)")
        self.c2 = self.p_gyr.plot(pen=pg.mkPen("r", width=2), name="gy (pitch)")

        self.info = QLabel("")
        self.info.setStyleSheet(AppStyles.get_status_label_style())
        self.info.setWordWrap(True)
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
