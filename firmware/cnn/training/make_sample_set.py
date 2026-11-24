"""
File:        make_sample_set.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-22
Version:     0.1.0
Description: Writes the small, fixed, made-up take sets the training tests
             use (cnn-record-train-handoff.md v0.4, TRN-1, TRN-3, TRN-4). The
             takes go through the record GUI's own snapshot parser and
             TakeStore, so they are real take files; the event shapes follow
             sim_source.py (pothole: nose down then up and a drop; speed bump:
             the reverse; false trigger: a short vertical spike). Every take
             has fw "sim-0.1.2-bench", so training refuses them unless
             simulated takes are allowed, and a fixed seed and fixed PC times,
             so every run of this script writes the same files.

               trn1  4 bench sessions x 3 classes x 10 takes: TRN-1 (two runs,
                     same numbers) and the pipeline end to end.
               trn3  3 sessions; one also holds 3 takes recorded with other
                     snapshot settings (n_pre 208, n_post 416) and 1 take cut
                     short: TRN-3 (set refused with the files named, bad take
                     rejected).
               trn4  3 sessions with only 5 FALSE_TRIGGER takes in all (thin
                     class), plus an empty file, an unparseable file and an
                     UNLABELED take: TRN-4 (skipped and listed, thin class
                     flagged, no crash).

             Usage (from this folder, with the shared venv, in Command Prompt):
               "%USERPROFILE%\\venvs\\ee475-cnn\\Scripts\\python.exe" make_sample_set.py
             Replaces sample_data\\trn1, trn3 and trn4 (only folders this script
             wrote; anything else is refused). Needs numpy.
"""

# ============================================================================
# Imports
# ============================================================================

from __future__ import annotations

import argparse
import binascii
import math
import shutil
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
CNN_DIR = HERE.parent
for _p in (HERE, CNN_DIR, CNN_DIR / "cnn_record_gui"):  # This folder, cnn_config, GUI modules
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import numpy as np  # noqa: E402  (needs the path above)

import cnn_config as cfg  # noqa: E402
from snapshot_protocol import EventLine, Snapshot, SnapshotAssembler  # noqa: E402
from take_store import TakeStore  # noqa: E402

# ============================================================================
# Constants
# ============================================================================

# Fixed seed; each set adds its index, so the sets do not depend on each other.
SAMPLE_SEED = 475

# gui_commit written into every sample take; also how this script recognizes
# the folders it may replace.
SAMPLE_COMMIT = "sample-set (make_sample_set.py)"

# PC time of the first take (2026-11-18) and the gap between takes, so the
# files are identical on every run.
BASE_WALL = 1795000000.0
WALL_STEP_S = 3.0

# The simulated device's BOOT line (sim_source.py), in the record build's format.
BOOT_FMT = ("BOOT,fw=%s,odr=416,acc_fs=16,gyr_fs=500,thr_z=1.000,thr_roll=60.0,"
            "thr_pitch=60.0,lockout_ms=1250,rearm_frac=0.50,rearm_hold_ms=200,hpf_hz=%s,"
            "print_every=8,settle_ms=2000,ble=RoadQuality,rec=1,snap_rate=%d,snap_pre=%d,"
            "snap_post=%d,snap_rows=%d,snap_ch=%s")

# ============================================================================
# Functions: one take
# ============================================================================


def event_rows(label: str, rng: np.random.Generator, n_pre: int, n_post: int,
               rate_hz: int) -> np.ndarray:
    """
    One made-up snapshot: noise before the trigger, then the class's shape
    with a random size and speed, and noise. Row n_pre crosses the 1 g threshold.
    @param label    POTHOLE, SPEED_BUMP or FALSE_TRIGGER (the true class).
    @param rng      Seeded generator.
    @param n_pre    Rows before the trigger.
    @param n_post   Rows from the trigger on.
    @param rate_hz  Sample rate.
    @return (n_pre + n_post, 3) float array: z_hp (g), roll, pitch (dps).
    """
    n = n_pre + n_post
    t = (np.arange(n) - n_pre) / float(rate_hz)    # s from the trigger
    after = (t >= 0).astype(float)
    ta = np.maximum(t, 0.0)
    z = rng.normal(0.0, 0.02, n)
    roll = rng.normal(0.0, 1.5, n)
    pitch = rng.normal(0.0, 1.5, n)
    amp = rng.uniform(1.2, 3.0)
    speed = rng.uniform(0.8, 1.2)                   # Stretches or squeezes the shape
    if label == "FALSE_TRIGGER":
        z += after * amp * np.exp(-ta / 0.03) * np.sin(2 * math.pi * 30 * speed * ta)
        roll += after * rng.uniform(5, 20) * np.exp(-ta / 0.05) * np.sin(
            2 * math.pi * 12 * speed * ta)
    else:
        sign = 1.0 if label == "POTHOLE" else -1.0
        ring = np.exp(-ta / 0.12)
        z += after * -sign * amp * ring * np.sin(2 * math.pi * 4 * speed * ta)
        pitch += after * sign * rng.uniform(80, 160) * ring * np.sin(2 * math.pi * 3 * speed * ta)
        roll += after * rng.uniform(10, 40) * ring * np.sin(2 * math.pi * 5 * speed * ta)
    z[n_pre] = 1.05 if z[n_pre] >= 0 else -1.05     # The threshold crossing
    return np.stack([z, roll, pitch], axis=1)


def snapshot_lines(rows: np.ndarray, seq: int, trig_idx: int, rate_hz: int, n_pre: int,
                   n_post: int, channels: str, fw: str) -> list:
    """
    The SNB / SNR / SNE lines exactly as the record firmware prints them
    (serial_out.cpp, send_snapshot), with a real CRC-16/CCITT-FALSE.
    @return Lines without line endings.
    """
    lines = ["SNB,%d,%d,%d,%d,%d,%d,%s,%s" % (seq, trig_idx, rate_hz, n_pre, n_post,
                                             len(rows), channels, fw)]
    crc = 0xFFFF
    for i, (a, b, c) in enumerate(rows):
        line = "SNR,%d,%.4f,%.4f,%.4f" % (i, a, b, c)
        crc = binascii.crc_hqx(line.encode("ascii"), crc)
        lines.append(line)
    lines.append("SNE,%d,%d,%04X" % (seq, len(rows), crc))
    return lines


def evt_line(seq: int, trig_idx: int, rule: str, rows: np.ndarray, n_pre: int,
             rate_hz: int) -> str:
    """
    @return An EVT line in the firmware's format, peaks taken from the rows.
    """
    peaks = np.max(np.abs(rows[n_pre:]), axis=0)
    n_peak = trig_idx + 40
    return "EVT,%d,%d,%d,%s,%.3f,%.1f,%.1f,%d,0,%s,pitch" % (
        seq, n_peak, n_peak * 1000000 // rate_hz, rule, peaks[0], peaks[1], peaks[2],
        n_peak * 1000 // rate_hz, "DU" if rule == "POTHOLE" else "UD")


def cut_rows(path: Path, n_drop: int) -> None:
    """
    Drops the last n_drop data rows of a take, leaving its header as it was
    (a take of the wrong length).
    @param path    Take file.
    @param n_drop  Rows to drop.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    path.write_text("".join(lines[:len(lines) - n_drop]), encoding="utf-8", newline="")

# ============================================================================
# Classes
# ============================================================================


class SampleWriter:
    """Writes made-up takes into one set's folder through TakeStore."""

    def __init__(self, root: Path, rng: np.random.Generator) -> None:
        """
        @param root  The set's folder (one subfolder per session).
        @param rng   Seeded generator.
        """
        self.root = Path(root)
        self.store = TakeStore(self.root)
        self.rng = rng
        self.k = 0            # Takes written: gives each its seq, trig_idx and PC time

    def take(self, session: str, label: str, true_label: Optional[str] = None,
             n_pre: int = 104, n_post: int = 520, boot: bool = True, evt: bool = True,
             hpf: str = "0.50") -> Path:
        """
        Writes one take.
        @param session     Session name.
        @param label       Label saved with the take (may be UNLABELED).
        @param true_label  Class whose shape the rows have (default label).
        @param n_pre       Rows before the trigger.
        @param n_post      Rows from the trigger on.
        @param boot        Keep a BOOT line in the header (else "missing").
        @param evt         Save the matching EVT (else the rule fields are "missing").
        @param hpf         hpf_hz in the BOOT line.
        @return The take file.
        """
        rng = self.rng
        shape = true_label or label
        rate = cfg.SIM_RATE_HZ
        rows = event_rows(shape, rng, n_pre, n_post, rate)
        seq, trig_idx = self.k % 1000, 10000 + 2000 * self.k
        # Rule class: right most of the time for potholes and bumps; for a
        # false trigger it answers something else (it has no such class).
        right = {"POTHOLE": "POTHOLE", "SPEED_BUMP": "BUMP"}.get(shape)
        if right is not None and rng.random() < 0.85:
            rule = right
        else:
            rule = str(rng.choice(["POTHOLE", "BUMP", "UNKNOWN"]))
        lines = snapshot_lines(rows, seq, trig_idx, rate, n_pre, n_post, cfg.SIM_CHANNELS,
                               cfg.SIM_FW)
        lines.append(evt_line(seq, trig_idx, rule, rows, n_pre, rate))
        asm = SnapshotAssembler(cfg.SNAP_STALL_TIMEOUT_S, cfg.SNAP_MAX_ROWS)
        items = [item for line in lines for item in asm.feed(line, 0.0)]
        snap = next(i for i in items if isinstance(i, Snapshot))
        event = next(i for i in items if isinstance(i, EventLine))
        boot_raw = BOOT_FMT % (cfg.SIM_FW, hpf, rate, n_pre, n_post, n_pre + n_post,
                               cfg.SIM_CHANNELS) if boot else None
        path = self.store.save_take(session=session, label=label, snap=snap,
                                    evt=event if evt else None, boot_raw=boot_raw,
                                    gui_commit=SAMPLE_COMMIT,
                                    received_wall=BASE_WALL + WALL_STEP_S * self.k,
                                    evt_missing_reason="no EVT within 5.0 s")
        self.k += 1
        return path

    def session(self, session: str, counts: dict) -> list:
        """
        Writes a session's takes, classes interleaved as on a bench.
        @param session  Session name.
        @param counts   {label: takes}.
        @return The take files.
        """
        left = dict(counts)
        paths = []
        while any(left.values()):
            for label in list(left):
                if left[label]:
                    # Every 2nd take keeps its BOOT line, every 9th lost its EVT.
                    paths.append(self.take(session, label, boot=self.k % 2 == 0,
                                           evt=self.k % 9 != 4))
                    left[label] -= 1
        return paths

# ============================================================================
# Functions: the sets
# ============================================================================


def build_trn1(w: SampleWriter) -> None:
    """
    TRN-1 / pipeline: 4 bench sessions, 10 takes per class each (40 per class).
    @param w  Writer for the set's folder.
    """
    for k in range(1, 5):
        w.session("sample%d_bench" % k, {c: 10 for c in cfg.CLASSES})


def build_trn3(w: SampleWriter) -> None:
    """
    TRN-3: a mixed set (3 takes with n_pre 208 / n_post 416 in mixC_bench)
    plus one take cut short.
    @param w  Writer for the set's folder.
    """
    for name in ("mixA_bench", "mixB_bench", "mixC_bench"):
        w.session(name, {c: 4 for c in cfg.CLASSES})
    for _ in range(3):
        w.take("mixC_bench", "POTHOLE", n_pre=208, n_post=416)
    cut_rows(w.take("mixC_bench", "SPEED_BUMP"), 24)


def build_trn4(w: SampleWriter) -> None:
    """
    TRN-4: FALSE_TRIGGER thin (2 + 2 + 1 takes), an UNLABELED take, an empty
    file and an unparseable file.
    @param w  Writer for the set's folder.
    """
    for name, n_false in (("thinA_bench", 2), ("thinB_bench", 2), ("thinC_bench", 1)):
        w.session(name, {"POTHOLE": 12, "SPEED_BUMP": 12, "FALSE_TRIGGER": n_false})
    w.take("thinA_bench", cfg.UNLABELED, true_label="POTHOLE")
    (w.root / "thinB_bench" / "POTHOLE-900.csv").write_bytes(b"")
    (w.root / "thinC_bench" / "SPEED_BUMP-900.csv").write_text(
        "# take_format=1\n# label=SPEED_BUMP\n# gui_commit=%s\n0.0100,abc,0.3000\n"
        % SAMPLE_COMMIT, encoding="utf-8", newline="\n")


# Set name -> builder, in seed order.
SETS = {"trn1": build_trn1, "trn3": build_trn3, "trn4": build_trn4}


def _written_here(folder: Path) -> bool:
    """
    @param folder  An existing set folder.
    @return True if every CSV in it is empty or carries SAMPLE_COMMIT, so
            replacing it cannot destroy recorded takes.
    """
    marker = "# gui_commit=%s" % SAMPLE_COMMIT
    for path in folder.rglob("*.csv"):
        if path.stat().st_size == 0:
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            head = [fh.readline().rstrip("\r\n") for _ in range(8)]
        if marker not in head:
            return False
    return True


def build_sets(out_dir: Path, names: tuple = tuple(SETS)) -> dict:
    """
    Writes (or replaces) the sample sets.
    @param out_dir  Folder that receives one folder per set.
    @param names    Which sets.
    @return {set name: its folder}.
    @raise RuntimeError if a set folder holds files this script did not write.
    """
    out_dir = Path(out_dir)
    written = {}
    for index, name in enumerate(SETS):
        if name not in names:
            continue
        target = out_dir / name
        if target.exists():
            if not _written_here(target):
                raise RuntimeError("%s holds takes this script did not write; move them "
                                   "first" % target)
            shutil.rmtree(target)
        SETS[name](SampleWriter(target, np.random.default_rng(SAMPLE_SEED + index)))
        written[name] = target
    return written


def main(argv: Optional[list] = None) -> int:
    """
    @param argv  Arguments (default sys.argv[1:]).
    @return Exit code.
    """
    parser = argparse.ArgumentParser(description="Write the made-up take sets for TRN-1, "
                                                 "TRN-3 and TRN-4.")
    parser.add_argument("--out", type=Path, default=cfg.SAMPLE_DATA_DIR,
                        help="where the sets go (default %(default)s)")
    args = parser.parse_args(argv)
    try:
        written = build_sets(args.out)
    except RuntimeError as exc:
        print("Stopped: %s" % exc)
        return 1
    for name, folder in written.items():
        n = sum(1 for _ in folder.rglob("*.csv"))
        print("%s: %d files in %s" % (name, n, folder))
    return 0


if __name__ == "__main__":
    sys.exit(main())
