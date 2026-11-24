# CNN Training: Window Cutting

This folder will hold the tools that train the road-quality CNN. Right now it
holds the first step, the **window cutter** (`windows.py`). The model training
and export come in a later step.

The record GUI (`..\cnn_record_gui\`) saves **clips**: 6 seconds of readings
around each key press (1 second before, 5 seconds after), plus long NONE clips
of normal road. The model looks at shorter pieces, **windows** of 2 seconds,
because the CNN will run on the board on a sliding window. The window cutter
makes those windows from the clips every time training runs:

- In each **POTHOLE** or **SPEED_BUMP** clip it finds the bump (the strongest
  movement), then cuts **5 windows** that each hold the bump somewhere between
  20% and 80% of the window. Placing the bump at different spots teaches the
  model to recognize an event anywhere in the window.
- From every **NONE** clip, short or long, it cuts a window every second.
- A window that crosses lost readings, a board reset, or readings taken before
  the board was settled (`ok=0`) is skipped and listed.

It never changes a clip file. Everything it decides is listed in a report,
`windows_report.csv`, so you can check any clip against its plot in the record
GUI.

Clips you deleted (in a session's `_deleted` folder) are never used. The label
comes from the file name, so your relabels in the GUI count.

The earlier training program (`train.py` and its Training tab) was retired on
2026-11-23 because it was built for the old 1.5-second snapshots. It is in git
under the tag `pre-manual-overhaul`.

---

## Before you start

- Do Part 1 of `..\cnn_record_gui\README.md` (Python 3.11 and the add-on folder
  `%USERPROFILE%\venvs\ee475-cnn`). The window cutter needs only numpy, which
  that installs.
- Record some clips (Parts 5 to 7 of the record GUI's README).

`requirements.txt` in this folder pins TensorFlow 2.15 for the training step
that comes later. The window cutter doesn't need it; don't install it unless
the project owner asks.

---

## Part 1: Cut windows

1. Open Command Prompt or PowerShell.
2. Go to this folder (`firmware\cnn\training`). With your own path:

   Command Prompt:

   ```
   cd /d "C:\Users\you\Documents\GitHub\ee475aut26team5\firmware\cnn\training"
   ```

   PowerShell:

   ```
   cd "C:\Users\you\Documents\GitHub\ee475aut26team5\firmware\cnn\training"
   ```

3. Run the cutter on every session:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" windows.py
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" windows.py
   ```

   Or only some sessions, by name (the same in both windows after the program
   path):

   ```
   ... windows.py --sessions 2026-11-24_bench 2026-11-30_car_route1
   ```

It prints one line with the number of windows per class, then any warnings,
then the folder it wrote to: `runs\windows_<date>_<time>\` in this folder. That
folder is not in git; keep the ones worth keeping elsewhere.

**Options:**

| Option | What it does |
| --- | --- |
| `--sessions A B ...` | Use only these sessions (default: all) |
| `--window-s 1.5` | Window length in seconds (default 2.0). At 1.5 or less, older 624-row takes are used too |
| `--decimate 4` | Keep every 4th reading, after a filter that removes what would alias (default 1 = keep all, 416 per second) |
| `--data-dir PATH` | Read sessions from another folder (default `firmware\cnn\data`) |
| `--out PATH` | Write the results to this folder |
| `--no-npz` | Write only the report |

---

## Part 2: What it writes

- **`windows_report.csv`**: one row per file, with these columns:
  - `session`, `file`, `label`;
  - `kind`: `clip`, or `take` for an older snapshot-era take;
  - `source`: `manual` (a key press), `long` (a long NONE recording) or `take`;
  - `rows`;
  - `peak_s`: for POTHOLE and SPEED_BUMP clips, where the bump was found, in
    seconds from the clip's start. The record GUI's clip plot uses the same
    time axis, so you can check it there;
  - `windows_cut`, `windows_skipped`, and `skip_reasons` (for example `gap:2`:
    `gap` is lost readings, `reset` a board reset, `ok0` readings before the
    board was settled);
  - `note`: why a file gave fewer windows or none (too short, the bump too near
    an end, a label that isn't a class, an unreadable file, ...).
- **`windows.npz`**: the windows themselves, for the training step: `X`
  (windows × samples × 3: z_hp, roll, pitch), `y` (0 = POTHOLE,
  1 = SPEED_BUMP, 2 = NONE), `sessions` and `clip_ids` (which session and clip
  each window came from), `classes`, `channels` and `rate_hz`.

**Warnings.** Below 30 clips of a class, or windows from fewer than 3 sessions,
it prints a warning that test scores will be noisy. It never refuses to run.

**Splitting (decision W3, for the training step).** Hold out whole sessions,
never single clips or windows: the windows of one clip are near copies, so a
random split would test the model on data it has already seen. `sessions` and
`clip_ids` are there for that.

---

## Tests

There are two kinds of tests:

- the **automatic checks**, which the computer runs by itself in a few seconds;
- tests **WIN-1 to WIN-4**, which you do by hand.

All of WIN-1 to WIN-4 are **NOT TESTED** so far. Only the project owner marks a
test as passed.

### Automatic checks

**What they check:** on made-up clips, the same results from two runs; the
bump found within 25 ms of where it was put; 5 windows per event clip, each
with the bump between 20% and 80%; NONE windows every second; a bump near the
start, a clip shorter than a window, and an older take; windows across lost
readings, a reset or `ok=0` rows skipped and listed; empty and unreadable files
listed without a crash; the warning; no clip file changed; the decimation
filter.

**What to do:** in this folder:

Command Prompt:

```
"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

PowerShell:

```
& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

**What you should see:** `Ran 14 tests in ...` and `OK` on the last line.

### WIN-1: Same seed, same windows

**Do:** run the automatic checks.

**You should see:** all pass (one of them runs the cutter twice and compares
`X`, `y` and the clip IDs).

**Proof:** the test output.

### WIN-2: Finding the bump

**Do:** run the automatic checks, then run the cutter on the MAN-2 session (see
the record GUI's README). Open `windows_report.csv`, pick 5 real POTHOLE or
SPEED_BUMP clips, open each in the record GUI, and compare its `peak_s` with
where the bump is in the clip plot.

**You should see:** the automatic checks pass; each event clip gives 5 windows;
NONE windows step by 1 second; every clip is listed in the report; for the 5
clips you checked, `peak_s` is at the bump.

**Proof:** the test output, the report, and your notes on the 5 clips.

### WIN-3: Edge cases

**Do:** run the automatic checks. Optionally, run the cutter on a session that
has older takes, with `--window-s 2.0` and then `--window-s 1.5`.

**You should see:** a bump in the first 0.3 s gives only windows that fit (in a
2 s window it can't sit 20% in, so none); a clip shorter than the window gives
none and is listed; an older 624-row take is skipped at 2.0 s and used at 1.5 s.

**Proof:** the test output and the report.

### WIN-4: Damaged data

**Do:** run the automatic checks.

**You should see:** windows across lost readings, a reset or `ok=0` rows are
skipped and listed by reason; an empty file and an unreadable file are listed;
the warning is printed; nothing crashes; no clip file changed (the test
compares each file's fingerprint before and after).

**Proof:** the test output.

---

## If something goes wrong

- **`No module named numpy`.** Use the program path shown above (the add-on
  folder's Python), not plain `python`.
- **0 windows.** Check the report's `note` column. Common reasons: the session
  name is wrong (`--sessions`), the clips are labeled FALSE_TRIGGER or
  UNLABELED (older takes: relabel them in the record GUI), or every clip is
  shorter than the window.
- **A clip says "rate ... differs".** All windows in one run must have the same
  sample rate. Run the sessions recorded at the other rate separately.

---

## For developers

`windows.py` (NumPy only) reads clips with `clip_file.load_clip()` from the
record GUI's folder, so the GUI and training share one parser. Everything is
in `build_windows(data_dir, sessions, opts) -> WindowSet`; the command line only
writes its results.

- **Peak** (`find_peak`): each channel divided by its median absolute value over
  the clip's `ok` rows (a channel whose median is 0 is left unscaled), the sum
  of the three squares, 0 on `ok=0` rows, a `PEAK_SMOOTH_S` (21-sample) moving
  average, then the highest point.
- **Event windows** (`_event_starts`): every start that keeps the window inside
  the clip and the peak between `PEAK_LO` and `PEAK_HI`; up to `K_PER_EVENT` of
  them, all different, drawn with `numpy.random.default_rng([WINDOW_SEED,
  crc32("<session>/<file>")])`. Seeding per clip keeps a clip's windows the same
  when other clips are added.
- **Damage** (`damage`): a window is skipped if, inside it, `n` goes down
  (reset), jumps by more than 1 (gap), or any row has `ok=0`. Nothing is drawn
  in its place. With decimation the check also covers half the filter's length
  on each side.
- **Decimation** (`lowpass_taps`): a windowed-sinc FIR (Hamming window,
  `DECIMATE_TAPS_PER_FACTOR × factor + 1` taps, cut-off at
  `DECIMATE_CUTOFF_FRAC` of the new Nyquist frequency, unity gain at 0 Hz), run
  over the whole clip, then every `factor`-th sample.
- **Older takes** (`take_format=1`, 3 columns, no `n` or `ok`) are used only at
  `OLD_TAKE_MAX_WINDOW_S` or less, and are never damage-checked (their snapshots
  were checked on the board).

All settings are in `..\cnn_config.py` under "training windows (request C)",
each marked DRAFT until the owner sets it.
