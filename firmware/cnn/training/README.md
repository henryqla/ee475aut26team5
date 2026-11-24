# CNN Training

This folder turns the clips you record into the model that runs on the board.

- **`train.py`** does everything in one command: it cuts windows, trains the
  model, tests it the way the board will run it, writes a report, and writes
  `firmware\firmware-bench\model_data.h` for the firmware.
- **`windows.py`** is its first step, the window cutter, which you can also run
  on its own.

**How the model gets better from here.** The model, the training and the export
are built once and then left alone. Every improvement comes from recording more
data:

1. Record and review clips in the record GUI's Record tab.
2. Run `train.py` (Part 1). Read the report.
3. Verify and upload `firmware-bench` in the Arduino IDE.
4. Watch the model on the board. Where it misses or false-alarms, record more
   clips of that kind and repeat.

## Windows

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

The earlier training program (built for the old 1.5-second snapshots) was
retired on 2026-11-23 and is in git under the tag `pre-manual-overhaul`. The
`train.py` here is a new program.

---

## Before you start

1. Do Part 1 of `..\cnn_record_gui\README.md` (Python 3.11 and the add-on folder
   `%USERPROFILE%\venvs\ee475-cnn`).
2. Install TensorFlow 2.15 into that folder, once (about 400 MB). Ask the project
   owner first. In this folder:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m pip install -r requirements.txt
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m pip install -r requirements.txt
   ```

   Keep the add-on folder where it is: TensorFlow's files are too deep for
   Windows if the folder sits inside the repository.
3. Record some clips (Parts 5 to 7 of the record GUI's README). You need clips
   of all three classes; two or more sessions give test scores.

The window cutter alone (Part 2) needs only numpy, which step 1 installs.

---

## Part 1: Train a model

1. Open Command Prompt or PowerShell and go to this folder
   (`firmware\cnn\training`), with your own path:

   Command Prompt:

   ```
   cd /d "C:\Users\you\Documents\GitHub\ee475aut26team5\firmware\cnn\training"
   ```

   PowerShell:

   ```
   cd "C:\Users\you\Documents\GitHub\ee475aut26team5\firmware\cnn\training"
   ```

2. Train on every session, with the newest one held out for testing:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" train.py
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" train.py
   ```

   It takes a minute or a few, printing one line per training round (epoch).
   The last lines say where the report is and whether `model_data.h` was
   written.

3. Open the report (`runs\<date>-<time>\report.txt`) and read it (see "Reading
   the report" below).
4. If `model_data.h` was written: open `firmware\firmware-bench` in the Arduino
   IDE, Verify, and Upload. The board's boot self-test checks it got the same
   model.

**Choosing sessions** (the same in both windows after the program path):

```
... train.py --test-sessions 2026-12-02_car     # hold out these sessions instead
... train.py --sessions bench_1 bench_2         # use only these sessions
```

"Newest" means the session whose clips were recorded last (by the press times
inside the clips, not by the folder name). With only one session, it trains on
everything and the report says the test scores are missing.

**Options:**

| Option | What it does |
| --- | --- |
| `--sessions A B ...` | Use only these sessions (default: all) |
| `--test-sessions A ...` | Hold these out for testing (default: the newest; none if there is only one) |
| `--synthetic` | Train on made-up clips instead of recordings (see below) |
| `--data-dir PATH` | Read sessions from another folder (default `firmware\cnn\data`) |
| `--fault bad-op` / `--fault mismatch` | Test hooks for TRN-4 only: make an export check fail on purpose |
| `--model-data PATH`, `--runs-dir PATH` | For developers: write `model_data.h` or the run folders somewhere else |

**Exit codes:** 0 = `model_data.h` written; 1 = an export check failed (nothing
written); 2 = stopped before training (the report says why).

### What it does

1. Cuts windows from the chosen sessions with the window cutter (Part 2),
   always at the board's full 416 readings per second.
2. Stops before training if a class has no clips in the training sessions, and
   says which.
3. Sets aside 15% of each class's training clips (whole clips) to decide when
   to stop training, and weights the classes so a class with fewer windows
   counts as much.
4. Trains the fixed model. It is always the same model (settings in
   `..\cnn_config.py`, "training and export"); you don't have to change them.
   Two runs on the same clips give the same model and the same report.
5. Tests it on the held-out sessions, the way the board will run it (below).
6. Converts it for the board and checks it (below). Only if every check passes
   is `model_data.h` written, and the old one is replaced in one step. If a
   check fails, the old `model_data.h` stays exactly as it was.

It never changes, moves or deletes a clip.

### Reading the report

`report.txt` has six parts:

1. **Data:** clips and windows per class, per session, and in each split.
2. **Training:** the model size, the scaling it learned, the class weights,
   and when training stopped.
3. **Window scores:** on the held-out windows, how often the model's top class
   is the true class (confusion matrix and recall per class).
4. **Streaming test:** each held-out clip is scanned like the board does: a
   2-second window every 0.5 seconds, a detection when the top class is POTHOLE
   or SPEED_BUMP with at least the cutoff probability, then no new detection for
   2 seconds. For each cutoff from 0.50 to 0.95:
   - **once**: the share of event clips detected exactly once, as the right
     class (this is the number to make high);
   - **wrong class**, **double** (detected twice or more), **missed**;
   - **false alarms/h**: detections in held-out NONE clips per hour of NONE
     recording ("missing" if no NONE clip was held out).
5. **Export checks:** input and output shapes, only ops the board supports,
   under 256 KB, and the converted model agrees with the trained one.
6. **Files** in the run folder.

**Choosing the cutoff.** `train.py` never picks one. Use the table to choose a
cutoff that detects most events once without too many false alarms, then set
`DET_CONF` in `firmware\firmware-bench\config.h` by hand. Flashing a new model
does not change it.

### Synthetic clips

`--synthetic` trains on made-up clips (bumps shaped like the simulated device's,
plus noise), written only inside the run folder, never in `data\`. Its
`model_data.h` has a `MODEL_VERSION` that starts with `synthetic-`. It is for
testing the tools and the firmware before real clips exist. Three made-up
sessions: `syn_1` and `syn_2` (all classes; the default trains on `syn_1` and
holds out `syn_2`) and `syn_no_none` (no NONE clips, for the boundary tests).

---

## Part 2: Cut windows only

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

## Part 3: What the tools write

**`train.py`** writes a run folder, `runs\<YYYYMMDD-HHMMSS>\` (not in git; keep
the ones worth keeping elsewhere):

- `report.txt`: the report (Part 1);
- `windows_report.csv`: what each clip gave (below);
- `model.keras` and `model.tflite`: the trained and the converted model;
- `model_data.h`: a copy of what went to `firmware-bench` (only if every export
  check passed);
- `config.json`: every setting the run used, the scaling and the class weights;
- `synthetic_data\`: the made-up clips (`--synthetic` only).

**`firmware\firmware-bench\model_data.h`** is generated: never edit it by hand,
run `train.py` again instead. It holds the model, its version (the run ID), the
class names in output order, the window size (832 readings), the hop (208), the
rate (416 per second), the channel order, and 3 test windows (one per class)
with the outputs the board must reproduce at boot. It holds no cutoff:
`DET_CONF` lives only in `config.h`.

**`windows.py`** writes:

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

**Splitting (decision W3).** Hold out whole sessions, never single clips or
windows: the windows of one clip are near copies, so a random split would test
the model on data it has already seen. `sessions` and `clip_ids` are there for
that, and `train.py` splits this way.

---

## Tests

There are two kinds of tests:

- the **automatic checks**, which the computer runs by itself in under a
  minute;
- tests **TRN-1 to TRN-4** (`train.py`) and **WIN-1 to WIN-4** (the window
  cutter), which you do by hand.

All of them are **NOT TESTED** until the project owner marks them. Only the
project owner marks a test as passed.

### Automatic checks

**What they check:**

- **The window cutter:** on made-up clips, the same results from two runs;
  the bump found within 25 ms of where it was put; 5 windows per event clip,
  each with the bump between 20% and 80%; NONE windows every second; a bump
  near the start, a clip shorter than a window, and an older take; windows
  across lost readings, a reset or `ok=0` rows skipped and listed; empty and
  unreadable files listed without a crash; the warning; no clip file changed;
  the decimation filter.
- **`train.py`:** the newest session found by press time; one session; missing
  sessions; the validation split keeps clips whole; class weights; the
  made-up clips are readable and the same every run; the model's size (6,147
  numbers) and ops; short runs on made-up clips that write `model_data.h`, fail
  an export check on purpose (the old `model_data.h` stays byte for byte), stop
  on a class with no clips, and report false alarms as missing without NONE
  clips.
- **The board's rule and the export:** windows every 0.5 s, the 2 s lockout,
  the cutoff, the outcomes, the cutoff table, each export check, and
  `model_data.h` (its numbers read back exactly; no cutoff in it).

The model tests need TensorFlow; without it they are skipped.

**What to do:** in this folder:

Command Prompt:

```
"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

PowerShell:

```
& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

**What you should see:** `Ran 63 tests in ...` and `OK` on the last line.

### TRN-1: Fresh install, same report twice

**Do:**

1. Make a new, empty Python 3.11 folder (venv) at a short path, for example
   `C:\Users\you\AppData\Local\Temp\ee475-trn1`, and install only
   `requirements.txt` into it. Save `pip freeze` from it.
2. With that folder's Python, run the automatic checks, then
   `train.py --synthetic` twice.
3. Compare the two `report.txt` files.
4. Delete the folder when you are done.

**You should see:** the checks pass; both runs write a run folder and
`model_data.h`; the two reports are the same except the Run, Date, Duration and
Model lines (the run ID and the time).

**Proof:** `pip freeze`, the test output, both reports.

### TRN-2: A full synthetic run

**Do:** run `train.py --synthetic` (it holds out `syn_2`).

**You should see:** the report has the confusion matrix, the streaming results,
false alarms per hour and the cutoff table; every export check passes;
`model_data.h` has 3 test windows and their outputs, and `MODEL_VERSION` starts
with `synthetic-`.

**Proof:** the report and the top of `model_data.h`.

### TRN-3: Edge cases

**Do** (the same in both windows after the program path):

```
... train.py --synthetic --sessions syn_1
... train.py --synthetic --sessions syn_1 syn_no_none --test-sessions syn_no_none
... train.py --synthetic --sessions syn_no_none
```

**You should see:** (a) one session: it trains on everything and the report
says the test scores are missing; (b) no held-out NONE clips: the report says
false alarms per hour is missing; (c) a class with no clips: it stops before
training, names NONE, and exits with code 2.

**Proof:** the console output and the reports.

### TRN-4: Export check fails

**Do:** note the fingerprint of `firmware\firmware-bench\model_data.h`
(PowerShell: `Get-FileHash ..\..\firmware-bench\model_data.h`), then run:

```
... train.py --synthetic --fault bad-op
... train.py --synthetic --fault mismatch
```

and take the fingerprint again.

**You should see:** both runs end with "an export check failed" (exit code 1);
the reports say `FAILED (allowed ops)` and `FAILED (TFLite matches Keras)`; no
`model_data.h` in either run folder; the fingerprint is unchanged.

**Proof:** the console output, the reports, and both fingerprints.

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
- **`No module named tensorflow`.** Install `requirements.txt` (Before you
  start, step 2), with the add-on folder's Python.
- **"Stopped before training: no NONE windows ..."** Every class needs clips in
  the training sessions. Record some, relabel clips in the GUI, or choose other
  sessions.
- **"an export check failed".** Section 5 of the report says which check. The
  old `model_data.h` was not touched. Don't edit `model_data.h` by hand; tell
  the project owner which check failed.
- **The streaming test shows many "double".** One bump stays inside about four
  overlapping windows. A double count means the model still calls it more than
  2 seconds later; more clips usually help. The lockout (2 s) is a firmware
  setting the project owner decides.

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

The window settings are in `..\cnn_config.py` under "training windows
(request C)", each marked DRAFT until the owner sets it.

**`train.py`** (`run_training(RunOptions) -> RunResult`; the command line only
fills the options):

- **Sessions** (`choose_sessions`): held out = `--test-sessions`, else the
  session with the latest `press_epoch_ms` in its clip headers
  (`session_press_ms`), else none.
- **Splits** (`validation_clips`): per class, `round(VAL_FRAC × clips)` whole
  clips (at least 1 when the class has 2 or more, and at least 1 left to train),
  drawn with `default_rng([TRAIN_SEED, class])`. Class weights:
  total / (classes × count). Scaling: the training windows' mean and standard
  deviation per channel, frozen into a Keras `Normalization` layer, which
  converts to TFLite `SUB` and `MUL`.
- **Model** (`build_model`): `cfg.MODEL_*` (E1). Keras 2 (TensorFlow 2.15).
  Determinism: `tf.keras.utils.set_random_seed` and
  `tf.config.experimental.enable_op_determinism`. Early stopping on the
  validation loss; the best epoch's weights are always restored (Keras 2 does
  it only when it stops early).
- **Synthetic clips** (`make_synthetic`): shapes and noise like
  `sim_source.SimDevice`, small symmetric road wobbles on every clip, one
  seeded generator per clip, so the files are byte-identical every run.

**`evaluate.py`** (NumPy only; the model is a `predict` function): the board's
windows (`scan_starts`: ends at row 831 + k × 208; windows with a gap, reset or
`ok=0` row are skipped), the detection rule (`detections`: top class not NONE,
probability ≥ cutoff, the next detection only once `DET_LOCKOUT_S` (2.0 s =
832 samples) has passed; ties go to the first class, as `argmax`), outcomes and
the cutoff table. Request F's firmware must follow the same rule.

**`export.py`**: float32 conversion; op list and facts read with
`BUILTIN_WITHOUT_DEFAULT_DELEGATES` (XNNPACK off, so the real ops show); the
Keras comparison runs on TensorFlow's reference kernels (`BUILTIN_REF`), the
closest to TFLite Micro; `model_data.h` is written to a `.tmp` file and swapped
in with `os.replace`. Adapted from Jake's ml-on-device-playground
(`toolkit.py`, `deploy.py`; MIT, see `LICENSE-ml-on-device-playground.txt`).

The training settings are in `..\cnn_config.py` under "training and export
(request E)". `TFLM_ALLOWED_OPS` must match the ops request F's firmware
registers, and `INFER_HOP_S` and `DET_LOCKOUT_S` must match the firmware's hop
and `DET_LOCKOUT_MS`.
