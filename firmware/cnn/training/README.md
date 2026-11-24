# CNN Training

This folder teaches a small computer model (a "CNN") to tell potholes, speed
bumps and false alarms apart, using the **takes** recorded with the CNN Record
GUI (`../cnn_record_gui/`). It then packs the model into files the firmware
will use later.

One **run** does everything in one go:

1. reads every take and checks that they belong together;
2. sets whole **sessions** aside for testing, so the model is always tested on
   recordings it never saw;
3. trains the model;
4. scores it on the test sessions, next to the board's own rule-based answer;
5. saves the model, a C header for the firmware, and a report.

You can start a run from the GUI's **Training** tab or from Command Prompt.
Both do exactly the same thing.

The guide has these parts:

- **Part 1:** install TensorFlow (once per computer).
- **Part 2:** start a run.
- **Part 3:** what a run saves.
- **Part 4:** how to read the report.
- **Part 5:** the made-up sample sets.
- **Part 6:** cut takes from drive logs and manual recordings.
- **Tests:** what to do and what you should see.
- **If something goes wrong.**

At the end, a short section for developers describes the files.

---

## Before you start

You need:

- the CNN Record GUI installed: `../cnn_record_gui/README.md`, Part 1. Training
  uses the same add-on folder, `venvs\ee475-cnn` in your user folder. It lives
  outside the project folder because TensorFlow's file names are too long for
  Windows' 260-character path limit there; no Windows setting needs changing.
- The project owner's OK before you install TensorFlow (Part 1). This is a
  project rule.
- Takes to train on. A model is only worth reporting with at least **30 takes
  of every class** for each session type (bench or car), and a run needs at
  least **3 sessions** (one to train on, one to decide when to stop training,
  one to test on).

Commands are typed in **Command Prompt** (Start button, type `cmd`, press
Enter) or **PowerShell** (for example VS Code's terminal). Where the two differ,
each command is shown for both: use the one that matches your window (in
PowerShell, each line starts with `PS`). The GUI guide explains this in
"Before you start".

---

## Part 1: Install TensorFlow (once per computer)

1. **Go to this folder.** In File Explorer, open the project folder, then
   `firmware`, `cnn`, `training`. Click the address bar and press Ctrl+C. In
   Command Prompt type `cd /d "`, in PowerShell type `cd "`; then press Ctrl+V,
   type `"` and press Enter. The text in front of the cursor now ends with
   `training>`.

2. **Install TensorFlow.** Get the project owner's OK first, then type:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m pip install -r requirements.txt
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m pip install -r requirements.txt
   ```

   This downloads TensorFlow 2.15, several hundred megabytes, and takes a few
   minutes. The last line starts with `Successfully installed`.

3. **Check it.** Type:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -c "import tensorflow as tf; print(tf.__version__)"
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -c "import tensorflow as tf; print(tf.__version__)"
   ```

   It should print `2.15.1` (after a few seconds, maybe with a few notice lines
   first).

4. **Save the list of installed versions.** Type:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m pip freeze > pip-freeze.txt
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m pip freeze | Out-File -Encoding utf8 pip-freeze.txt
   ```

   This writes `pip-freeze.txt` in this folder. The handoff asks for the
   versions actually installed. In PowerShell, don't use `>`: it saves the file
   as UTF-16, which many editors show with a space between every letter.

---

## Part 2: Start a run

### From the GUI

1. Start the CNN Record GUI (`../cnn_record_gui/README.md`, Part 3) and click the
   **Training** tab.
2. **Data folder** shows where takes are read from, normally
   `firmware\cnn\data`. **Choose folder...** picks another one, for example a
   sample set (Part 5).
3. In **Sessions**, untick any session you don't want. Each line shows the
   session's type and how many labeled takes it has.
4. Leave **Test sessions** and **Validation sessions** empty to let the program
   choose (always the same choice for the same sessions). To choose yourself,
   type session names separated by commas.
5. Tick **Allow simulated takes** only for the sample sets.
6. Press **Train**. The log on the right shows each step. The first run takes a
   few extra seconds to load TensorFlow.
7. When it finishes, the line under the buttons says **Done**, and whether the
   result is reportable. **Open run folder** shows the saved files.

**Stop** ends the run after the current training round and saves no model.

### From the command line

Go to this folder (Part 1, step 1), then type:

Command Prompt:

```
"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" train.py
```

PowerShell:

```
& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" train.py
```

That trains on every session in `firmware\cnn\data`. Extra options, typed after
`train.py`:

| Option | What it does |
| --- | --- |
| `--sessions A B C` | Use only these sessions |
| `--test-sessions C` | Test on these sessions (default: chosen by the program) |
| `--val-sessions B` | Use these to decide when to stop training (default: chosen by the program) |
| `--data-dir FOLDER` | Read takes from another folder |
| `--allow-sim` | Accept takes from the simulated device (sample sets only) |
| `--out-dir FOLDER` | Save runs somewhere else (default `training\runs`) |

The last line says where the run was saved.

---

## Part 3: What a run saves

Every run gets its own folder, `training\runs\<date>_<time>\`, for example
`runs\2026-11-22_153000\`. Runs are not saved in Git. Copy the ones worth
keeping somewhere safe.

| File | What it is |
| --- | --- |
| `report.md` | The report: data used, training, test results, export (Part 4) |
| `report.json` | The same numbers, for scripts |
| `train.log` | Everything the run printed. Failed runs have it too |
| `model_data.h` | The model and its input rules, for the firmware (next handoff) |
| `model.tflite` | The model in the small format the firmware runs |
| `model.keras` | The model in TensorFlow's own format |
| `frozen_test_set.npz` | The test takes and the model's answers, to check the board gets the same answers later |

---

## Part 4: How to read the report

- **Result: REPORTABLE / NOT REPORTABLE.** Not reportable means some class had
  fewer than 30 takes for a session type. The model is still saved, so the
  steps can be checked, but its numbers are too noisy to trust.
- **Data.** The sessions used, which side of the split each one was on, and how
  many takes of each class it had. Below that, any takes that were skipped
  (UNLABELED, empty, unreadable) or rejected (wrong length, bad label), each
  with the reason.
- **Confusion matrix.** One row per true class, one column per answer. The
  numbers on the diagonal are correct answers.
- **Rule baseline.** The board's own rule-based answer, saved in every take,
  scored on the same test takes. The rule has no "false trigger" answer, and an
  `UNKNOWN` or missing answer counts as wrong.
- **Per-class recall.** For each class, the share of its test takes that got the
  right answer, for the model and for the rule.
- **Metrics fingerprint.** A short code made from every number in the run. Two
  runs on the same takes give the same code (test TRN-1).
- **Keras vs TFLite.** How far the two model formats disagree on the test takes.
  It should be tiny. The owner sets the allowed limit (`TFLITE_MATCH_TOL` in
  `../cnn_config.py`); until then the report shows the difference without a
  verdict.

---

## Part 5: The made-up sample sets

The tests use three small sets of made-up takes. Make them (or remake them)
from this folder with:

Command Prompt:

```
"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" make_sample_set.py
```

PowerShell:

```
& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" make_sample_set.py
```

It writes `sample_data\trn1`, `sample_data\trn3` and `sample_data\trn4` and
prints how many files each has. The files are identical every time. They come
from the simulated device, so a run only accepts them with `--allow-sim` (or
**Allow simulated takes** ticked). The script only replaces folders it wrote
itself.

| Set | What's in it | Used by |
| --- | --- | --- |
| `trn1` | 4 bench sessions, 10 takes of each class in each | TRN-1 |
| `trn3` | 3 sessions; 3 takes recorded with other snapshot settings, and 1 take cut short | TRN-3 |
| `trn4` | 3 sessions with only 5 false-trigger takes, an empty file, an unreadable file and an UNLABELED take | TRN-4 |

---

## Part 6: Cut takes from drive logs and manual recordings

With the board's log build, the record GUI saves whole drives as **drive logs**
and keeps the 6 seconds around every **Record** press
(`../cnn_record_gui/README.md`, Part 11). `cut_windows.py` turns them into
ordinary takes that the GUI and a training run read like any other. Run it
after **Stop log**, from this folder, with the session's name:

Command Prompt:

```
"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" cut_windows.py --session 2026-11-25_car_route1
```

PowerShell:

```
& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" cut_windows.py --session 2026-11-25_car_route1
```

It makes, in this order:

1. **Manual recordings** in the session's `manual` folder that have no take yet.
   Record already made a take for most of them; those are skipped.
2. **One take per board event** (`EVT` line) in each drive log, placed exactly
   where the board's own snapshot would be. Its label comes from the last P, B
   or F mark pressed within 3 seconds after the event; with none, it is
   UNLABELED (relabel it in the GUI). An event inside a manual recording that
   has a take is skipped, so a bump is never saved twice.
3. **One take per mark with no event**: a bump the board's threshold missed.
   It is lined up at half the threshold in the 3 seconds before the mark. If
   nothing reaches half, it is lined up on the biggest movement there, however
   small. Like every take, it goes in the session folder, so training uses it
   however weak the bump was. The mark is listed for review only if nothing
   moved at all. Manual recordings (step 1) follow the same rule.
4. **Background windows**: 20 "nothing happening" pieces of each log (`--background`
   changes the number, 0 turns them off), at least 3 seconds from any event or
   mark, in the `background` folder. They are picked once per log.

**Rules:**

- A piece with missing readings, a board that was still settling, or not
  enough readings before or after is skipped and listed, never padded.
- Running it twice adds nothing: each take records which piece of which file it
  came from (`src_key`). A take you deleted is never made again.
- A take whose settings differ from the session's takes is refused and listed.
- It never changes or deletes a drive log or a manual recording.
- Training reads only the takes directly in the session folder, so
  `background` is left out unless moved in. Nothing is saved in `weak` any
  more. Takes that older versions put there still count as made, so they
  are never cut again; move them into the session folder (each under a number
  its label hasn't used) to train on them.
- A mark or Record press with no bump near it still gives a take, lined up on
  noise (its header's `peak_frac` is small). Delete it in the GUI.
- Takes cut from marks or by Record have no rule answer (`rule_class` is
  `missing`). The report counts those as the rule being wrong, so a test set
  with many of them makes the rule baseline look worse than it is.

It prints a summary and writes `cut_report.txt` in the session folder: takes
made per label and source, skipped pieces with reasons, items for review,
events covered by manual recordings, refused takes, and events with no mark.

---

## Tests

Each test says what to do, what you should see, and what to save as proof.
Only the project owner marks a test as passed.

### Automatic checks

From this folder, type:

Command Prompt:

```
"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

PowerShell:

```
& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

**You should see** `Ran 48 tests` and `OK` on the last line. Before TensorFlow
is installed, the last line says `OK (skipped=2)`: the two full-run tests wait
for TensorFlow. With TensorFlow they take about a minute. Ten of the tests
check `cut_windows.py` (CUT-1, CUT-3 and CUT-4 cases, and manual recordings);
they need no TensorFlow.

**It fails if** the last line says `FAILED`. Send all of the text to the owner.

### TRN-1: Same seed, same numbers

1. Make the sample sets (Part 5).
2. Run this twice:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" train.py --data-dir sample_data\trn1 --allow-sim
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" train.py --data-dir sample_data\trn1 --allow-sim
   ```

**You should see** both runs end with `Done in ... Run saved in ...`. The line
`Metrics fingerprint ...` shows the same code both times.

**It fails if** a run stops with an error, or the two codes differ.

**Proof to save:** `train.log` from both run folders.

### TRN-2: Train on bench sessions

Needs real bench sessions: at least 3, with 30 takes of each class in all.
The owner sets `TFLITE_MATCH_TOL` in `../cnn_config.py` first.

1. Run `train.py` (or press **Train** with the bench sessions ticked).

**You should see** in `report.md`: the confusion matrix, per-class recall, and
the rule baseline, all for the held-out sessions; `Result: REPORTABLE`; and
under Export, the Keras vs TFLite difference `within the tolerance`. The run
folder holds `model.tflite`, `model_data.h` and `frozen_test_set.npz`.

**It fails if** a file is missing, the result is not reportable, or the
difference is over the tolerance.

**Proof to save:** the whole run folder.

### TRN-3: Mixed settings and a take of the wrong length

1. Make the sample sets (Part 5).
2. Run:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" train.py --data-dir sample_data\trn3 --allow-sim
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" train.py --data-dir sample_data\trn3 --allow-sim
   ```

**You should see** the run stop, without training, after these lines:

- `Rejected 1 take(s):` and `mixC_bench/SPEED_BUMP-5.csv: wrong length: 600 rows, header says 624`;
- `Refusing a mixed set`, then two settings sets: the second has `n_pre=208`
  and lists `mixC_bench/POTHOLE-5.csv`, `-6.csv` and `-7.csv`.

**It fails if** the run trains anyway, a different file is named, or Python
prints a traceback (lines starting with `Traceback`).

**Proof to save:** the run folder's `train.log`.

### TRN-4: Bad files and a thin class

1. Make the sample sets (Part 5).
2. Run:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" train.py --data-dir sample_data\trn4 --allow-sim
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" train.py --data-dir sample_data\trn4 --allow-sim
   ```

**You should see** the run finish (`Done in ...`), and in the log:

- `Skipped 1 UNLABELED take(s)`;
- `Skipped 2 file(s):` listing `thinB_bench/POTHOLE-900.csv: empty file` and
  `thinC_bench/SPEED_BUMP-900.csv: unparseable ...`;
- `Thin class: 5 FALSE_TRIGGER take(s) in bench sessions, fewer than 30`;
- `Result: NOT REPORTABLE`.

**It fails if** the run crashes (a `Traceback`), or one of these lines is missing.

**Proof to save:** the run folder's `train.log` and `report.md`.

### CUT-1 to CUT-4 and MR-4: The cutter

All five are **NOT TESTED** so far.

- **CUT-1 (simulated):** the automatic checks include it: a take cut at each
  event of the simulated log build must equal that event's snapshot, row for
  row as text, with the same CRC. **Proof:** the automatic checks' output.
- **CUT-2 (board):** run the cutter (Part 6) on the sessions of the GUI's DRV-2
  and DRV-3 tests. **You should see** one take per event, labeled from its mark;
  every take opens in the GUI's Collected takes; 20 files per log in
  `background`, none within 3 seconds of an event or mark (the report lists
  them). **Proof:** `cut_report.txt` and a screenshot.
- **CUT-3 (edge cases):** a log with a gap across an event, an event at the very
  start of a log, and a mark with no event; manual recordings with a tap under
  half of the threshold, with one under a tenth, and with no movement at all.
  **You should see** the first two skipped with reasons, the mark cut as
  `marker_only`, the two tapped recordings cut into takes in the session folder
  (`align=peak`), and the one with no movement listed for review. No `weak`
  folder appears. The automatic checks include these cases. **Proof:**
  `cut_report.txt`.
- **CUT-4 (fault):** run the cutter twice, then on a session holding takes with
  different settings. **You should see** the second run add nothing ("Takes
  made: 0") and the odd takes refused and listed. **Proof:** both reports and
  folder listings.
- **MR-4 (consistency):** run the cutter on the GUI's MR-2 session. **You
  should see** "Takes made: 0" from manual recordings (Record already made
  them; the report counts them under "Already cut before"). To compare one by
  hand, copy a file from `manual` into a new session's `manual` folder, run the
  cutter on that session, and compare the take it makes with the GUI's (for
  example `fc /b` in Command Prompt, or `Compare-Object (Get-Content a)
  (Get-Content b)` in PowerShell): they are the same. **Proof:**
  `cut_report.txt` and the comparison.

---

## If something goes wrong

- **"TensorFlow is not installed"**: do Part 1.
- **"Splitting by session needs at least 3 sessions"**: record more sessions, or
  tick more of them.
- **"Refusing a mixed set"**: the takes were recorded with different settings
  (another firmware version, snapshot length or filter). Any change starts a new
  dataset: tick only the sessions of one set. The log names the odd files.
- **Every take rejected as "simulated take"**: those takes came from the
  simulated device. Only the sample sets should be used that way.
- **The pip install fails**: the add-on folder must be Python 3.11. Check with
  `"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" --version` (Command
  Prompt) or `& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" --version`
  (PowerShell). TensorFlow 2.15 does not support 3.12 or newer.
- **"TensorFlow could not be imported: No module named 'tensorflow.python'"**:
  the install stopped halfway, usually because the add-on folder's path is too
  long (an error mentioning "Long Path support" appeared during the install).
  Use the short folder from `../cnn_record_gui/README.md`, Part 1, step 5,
  delete its `Lib\site-packages\tensorflow` folder, and do Part 1 again.
- **PowerShell says `Unexpected token '-m'`, or the path is not found, or a
  folder named `%USERPROFILE%` appeared**: a Command Prompt command was typed
  into PowerShell. Use the PowerShell version (with `&` and
  `$env:USERPROFILE`), and delete any `%USERPROFILE%` folder it created.

---

## For developers

**Decisions this follows** (`cnn-record-train-handoff.md` v0.4, owner answers of
2026-11-22):

- **Split (C11).** By whole sessions: one in 5 (at least one) held out for test,
  then one in 5 of the rest (at least one) for validation, picked with seed 475
  unless named. Below 10 sessions that is leave-one-session-out. Then the
  training takes alone are oversampled with random copies until every class
  matches the largest. Copies never reach validation or test.
- **Normalization (C10).** A Keras `Normalization` layer with the per-channel
  mean and variance of the training takes (originals, not copies) frozen in, so
  the firmware feeds raw g and dps.
- **Versions (C12).** TensorFlow 2.15 (Keras 2) in the record GUI's venv, which
  lives at `%USERPROFILE%\venvs\ee475-cnn` (owner, 2026-11-22). The deepest
  file in the `tensorflow_intel` 2.15.1 wheel is 153 characters below
  `site-packages`, so the venv folder must stay under about 85 characters
  without Windows long-path support. The old `cnn_record_gui\.venv` was 90.
- **Rule baseline.** `BUMP` read as `SPEED_BUMP`; `UNKNOWN` and missing count as
  wrong for every class.
- **Repeatable runs.** `tf.keras.utils.set_random_seed`,
  `tf.config.experimental.enable_op_determinism()`, and
  `TF_ENABLE_ONEDNN_OPTS=0` before TensorFlow loads. Early stopping keeps the
  best epoch's weights even when it never triggers.
- **Preprocessing settings.** Take headers don't carry the filter settings (the
  SNB line has no room for them, and a native USB reset often loses BOOT). `fw`
  stands in for them: **any change to the HPF, full scales or axis mapping must
  bump `FW_VERSION`**. BOOT values are compared whenever takes kept them, and
  `model_data.h` defines them only if known.

**model_data.h** holds: generation time, script version, commit, sessions and
take counts; `CNN_MODEL_VERSION`; the input contract, each macro named after
the `config.h` setting it must equal (`CNN_N_ROWS` = `SNAP_ROWS`,
`CNN_HPF_CUTOFF_HZ` = `HPF_CUTOFF_HZ`, ...); the class names in output order;
the TFLite op list (`CNN_OPS`, for a trimmed op resolver); and the model as a
16-byte-aligned `static` array, so include it from one `.cpp` only. The op list
uses TensorFlow's `Interpreter._get_ops_details()`, which has no public
equivalent in 2.15; if a future version drops it, the list comes out empty and
the report says so.

**Code.**

| File | What it does |
| --- | --- |
| `train.py` | The run: load, split, train, score, export, report; also the command line |
| `take_loader.py` | Reads and checks take files (uses `take_store.load_take` from the GUI) |
| `dataset_split.py` | Session split and oversampling |
| `eval_metrics.py` | Confusion matrix, recall, accuracy, rule baseline |
| `cnn_model.py` | The CNN, normalization inside (imports TensorFlow) |
| `model_export.py` | TFLite conversion, op list, frozen test set, `model_data.h` |
| `make_sample_set.py` | Writes the made-up sample sets |
| `cut_windows.py` | Cuts takes from drive logs and manual recordings (standard library only; the alignment and take format come from `../cnn_record_gui/take_cut.py`, shared with the GUI's Record button) |
| `requirements.txt` | TensorFlow pin, installed into the GUI's venv |
| `../cnn_config.py` | Every adjustable setting, in the "training and export" section |
| `../cnn_record_gui/training_widget.py` | The GUI's Training tab |
| `tests/` | The automatic checks |

**Credits.** `cnn_model.py`, `model_export.py`, `dataset_split.py`, `train.py`
and the GUI's `training_widget.py` adapt parts of `Python/training_widget.py`
from
[jake-g/ml-on-device-playground](https://github.com/jake-g/ml-on-device-playground),
a fork of [anandghanw/ml-on-device-playground](https://github.com/anandghanw/ml-on-device-playground),
under the MIT license. See `LICENSE-ml-on-device-playground.txt`; each adapted
file says in its header what it came from and what changed.
