"""
File:        helpers.py
Authors:     Henry (owner); first draft written with Claude
Date:        2026-11-23
Version:     0.2.0
Change:      0.2.0 (manual-recording-overhaul-handoff.md, request C): the
             TensorFlow check is gone with the retired training code. New:
             synthetic clips for the window cutter's tests, written with the
             record GUI's own header code (clip_file.clip_header), with a bump
             at a known row, jumps in n, resets and ok=0 rows; and an older
             snapshot-era take.
Description: Shared test helpers for the training folder. Puts the training
             folder, firmware/cnn (cnn_config) and the record GUI folder
             (clip_file, take_store) on sys.path.
"""

# ============================================================================
# Imports
# ============================================================================

import math
import random
import sys
from pathlib import Path

TRAINING_DIR = Path(__file__).resolve().parents[1]
CNN_DIR = TRAINING_DIR.parent
for _p in (TRAINING_DIR, CNN_DIR, CNN_DIR / "cnn_record_gui"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import cnn_config as cfg  # noqa: E402  (needs the path above)
from clip_file import ClipFacts, clip_header  # noqa: E402

# ============================================================================
# Constants
# ============================================================================

RATE = 416
BOOT = ("BOOT,fw=0.1.4-bench,odr=416,acc_fs=16,gyr_fs=500,hpf_hz=0.50,settle_ms=2000,"
        "bias_max=10.0,vert=az,roll=gy,pitch=gx,l_dec=4,tx_buf=32768,out_q=256,perf_ms=10000,"
        "imu_retry_ms=500,imu_good_reads=3,seg_ms=1000,cfg_ms=5000,ble=RoadQuality")

# ============================================================================
# Functions
# ============================================================================


def records(count, bump_at=None, amp=2.0, seed=1, gap_at=None, gap=20, reset_at=None,
            ok0=(), first_n=1):
    """
    Synthetic clip rows: sensor noise, plus an optional bump whose energy is
    centred on a known row (a Gaussian envelope on an 8 Hz carrier, symmetric
    about bump_at, so the true peak is exactly that row).
    @param count     Rows.
    @param bump_at   Row index of the bump's centre, or None.
    @param amp       Bump size (z_hp, g; pitch gets 40x that in dps).
    @param seed      Noise seed.
    @param gap_at    Row index before which `gap` samples are lost (n jumps), or None.
    @param gap       Samples lost at gap_at.
    @param reset_at  Row index at which n restarts at 1 (the board reset), or None.
    @param ok0       Row indexes sent with ok=0.
    @param first_n   n of the first row.
    @return list of (n, ok, z_hp, roll, pitch).
    """
    rng = random.Random(seed)
    sigma = 0.03 * RATE
    out = []
    n = first_n
    for i in range(count):
        if gap_at is not None and i == gap_at:
            n += gap
        if reset_at is not None and i == reset_at:
            n = 1
        z, roll, pitch = rng.gauss(0, 0.02), rng.gauss(0, 1.5), rng.gauss(0, 1.5)
        if bump_at is not None:
            d = i - bump_at
            env = math.exp(-0.5 * (d / sigma) ** 2)
            carrier = math.cos(2 * math.pi * 8 * d / RATE)
            z += amp * env * carrier
            pitch += 40 * amp * env * carrier
        out.append((n, i not in ok0, z, roll, pitch))
        n += 1
    return out


def write_clip(folder, name, recs, source=cfg.SOURCE_MANUAL, press_row=415):
    """
    Writes a clip_v1 file the way the record GUI does.
    @param folder     Session folder (created if needed).
    @param name       File name, e.g. "POTHOLE-1.csv" (the label comes from it).
    @param recs       records() output.
    @param source     cfg.SOURCE_MANUAL or cfg.SOURCE_LONG.
    @param press_row  Row of the press (manual clips).
    @return The path.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    facts = ClipFacts()
    for n, ok, *_ in recs:
        facts.add(n, ok)
    label = name.rpartition("-")[0]
    long_rec = source == cfg.SOURCE_LONG
    head = clip_header(source, label, folder.name, 1_790_000_000_000,
                       None if long_rec or not recs else recs[min(press_row, len(recs) - 1)][0],
                       None if long_rec or not recs else min(press_row, len(recs) - 1),
                       0.0 if long_rec else 1.0, facts.span / RATE if long_rec else 5.0, RATE,
                       facts, BOOT, "test", stop_epoch_ms=1_790_000_100_000 if long_rec else None)
    rows = "".join("%d,%d,0.0010,-0.0020,%.4f,%.4f,%.4f,0.2000,%.4f,%.4f,%.4f,%d\n"
                   % (n, int(round(n * 1e6 / RATE)), 1.0 + z, pitch + 0.4, roll - 0.3, z, roll,
                      pitch, 1 if ok else 0)
                   for n, ok, z, roll, pitch in recs)
    path = folder / name
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + rows)
    return path


def write_old_take(folder, name, bump_at=None, n_pre=104, n_post=520):
    """
    Writes an older snapshot-era take (three columns: z_hp, roll, pitch).
    @param folder   Session folder (created if needed).
    @param name     File name, e.g. "SPEED_BUMP-3.csv".
    @param bump_at  Row of a bump's centre, or None.
    @param n_pre    Rows before the trigger.
    @param n_post   Rows from the trigger on.
    @return The path.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    label = name.rpartition("-")[0]
    rows = n_pre + n_post
    head = ("# take_format=1\n# label=%s\n# fw=0.1.2-bench\n# rate_hz=416\n# n_pre=%d\n"
            "# n_post=%d\n# n_rows=%d\n# channels=az_hp/gx/gy\n" % (label, n_pre, n_post, rows))
    body = "".join("%.4f,%.4f,%.4f\n" % (z, roll, pitch)
                   for _n, _ok, z, roll, pitch in records(rows, bump_at=bump_at))
    path = folder / name
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(head + body)
    return path
