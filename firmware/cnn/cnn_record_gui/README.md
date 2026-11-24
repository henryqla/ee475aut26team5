# CNN Record GUI

This program records labeled pieces of motion data from the road-quality board.
Later, a computer model (a "CNN") will learn from these recordings to tell
potholes, speed bumps and normal road apart.

The board sends every sensor reading to the laptop, about 416 a second, and
decides nothing by itself. When the passenger sees a pothole or a speed bump
coming, they press one key:

- **P** for a pothole,
- **B** for a speed bump,
- **N** for nothing (normal road).

That one key both labels and records. The program saves a **clip**: the
readings from 1 second before the press to 5 seconds after it, in one small
file. Clips are grouped into **sessions**, for example one session for a day of
bench testing or one for each drive.

The program never throws a clip away, hides it, or marks it as bad. It shows you
each clip and some plain facts about it, and **you** decide whether to keep it,
relabel it or delete it.

**L** records a **long NONE** clip of normal road: press it once to start and
again to stop. **Backspace** deletes the last clip you saved.

You can also run the program without the board. It then uses a pretend board
(the **simulated device**), with buttons to make it misbehave on purpose. This
is useful for learning the program and for some of the tests.

The guide has three parts:

- **How to use the program:** Parts 1 to 10.
- **Tests:** what to do and what you should see.
- **If something goes wrong.**

At the end, a short section for developers describes the files.

---

## Before you start

You need:

- A Windows computer. The installation needs an internet connection; after that
  you can work offline.
- This project's files on that computer (the `ee475aut26team5` folder from GitHub).
- Python 3.11. Part 1, step 2 checks whether you already have it.
- For real recordings:
  - the ESP32-S3 DevKit board with the motion sensor wired to it;
  - a USB cable that carries data (some cables only charge);
  - the Arduino IDE, already set up for this board.
- The project owner's OK before you install the program's add-ons (Part 1,
  step 6). This is a project rule.

Two words used below:

- **Command Prompt** and **PowerShell** are the two kinds of window where you
  type commands. Type each command exactly as shown, then press Enter. Where
  the two need different commands, this guide shows both, first for
  **Command Prompt**, then for **PowerShell**. Use the one that matches your
  window: in PowerShell, each line starts with `PS`. The terminal inside VS Code
  is PowerShell unless you changed it.
- A **folder path** is the address of a folder, for example
  `C:\Users\you\Documents\GitHub\ee475aut26team5`.

---

## Part 1: Install the program (once per computer)

1. **Open a command window.** Click the Start button, type `cmd`, and press
   Enter: Command Prompt opens. PowerShell works too, for example VS Code's
   terminal (**Terminal > New Terminal**); then use the PowerShell commands.
   Don't mix them up: PowerShell doesn't understand `%USERPROFILE%`, so a
   Command Prompt command typed into PowerShell fails, or creates a folder
   literally named `%USERPROFILE%` in the wrong place.

2. **Check that Python 3.11 is installed.** Type this (the same in both windows)
   and press Enter:

   ```
   python --version
   ```

   It should answer `Python 3.11` followed by a dot and a number.

   - If it says Python is not found, go to
     https://www.python.org/downloads/windows/ and find **Python 3.11.9**. It is
     the last 3.11 version with a Windows installer. Click
     **Windows installer (64-bit)** and run the file you downloaded. On the
     installer's first screen, tick **Add python.exe to PATH**, then click
     **Install Now**. When it finishes, close the command window, open a new
     one, and check again.
   - If it shows a different version, such as 3.12 or 3.13, install 3.11.9 the
     same way. In step 5, type `py -3.11` instead of `python`.

3. **Find the program's folder.** Open File Explorer and go to the project
   folder, then into `firmware`, then `cnn`, then `cnn_record_gui`. Click once on
   the address bar at the top of the window. The address turns into text that
   is already selected. Press Ctrl+C to copy it.

4. **Go to that folder.** In Command Prompt, type `cd /d "`; in PowerShell,
   type `cd "`. Then paste the address with Ctrl+V, type a closing `"`, and press
   Enter. With your own address it looks like this:

   Command Prompt:

   ```
   cd /d "C:\Users\you\Documents\GitHub\ee475aut26team5\firmware\cnn\cnn_record_gui"
   ```

   PowerShell:

   ```
   cd "C:\Users\you\Documents\GitHub\ee475aut26team5\firmware\cnn\cnn_record_gui"
   ```

   The text in front of the blinking cursor now ends with `cnn_record_gui>`.

5. **Create the program's private add-on folder.** Type:

   Command Prompt:

   ```
   python -m venv "%USERPROFILE%\venvs\ee475-cnn"
   ```

   PowerShell:

   ```
   python -m venv "$env:USERPROFILE\venvs\ee475-cnn"
   ```

   Nothing is printed when it works. After a few seconds a new folder
   `venvs\ee475-cnn` appears in your user folder (`C:\Users\` followed by your
   name). The program's add-ons go into that folder, so they don't change
   anything else on the computer. The training tools (`..\training\`) use the
   same folder.

   The folder sits there, not inside the project folder, on purpose: the
   training add-ons have very long file names that would go over Windows'
   260-character limit inside the project folder, and it keeps OneDrive from
   syncing thousands of add-on files.

6. **Install the add-ons.** Get the project owner's OK first, then type:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m pip install -r requirements.txt
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m pip install -r requirements.txt
   ```

   In PowerShell, the `&` at the start is needed to run a program whose name
   is in quotes.

   This downloads four add-ons and takes a few minutes:
   - PyQt5 draws the window and plays the sounds.
   - pyqtgraph draws the plots.
   - numpy does the number crunching.
   - pyserial talks to the board over USB.

   When it finishes, the last line starts with `Successfully installed`. A notice
   that a newer version of pip is available is fine; ignore it.

7. **Save the list of installed versions.** Type:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m pip freeze > pip-freeze.txt
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m pip freeze | Out-File -Encoding utf8 pip-freeze.txt
   ```

   This creates a file called `pip-freeze.txt` that lists exactly what was
   installed. Test MAN-1 asks for this file. In PowerShell, don't use `>` here:
   it saves the file as UTF-16, which many editors show with a space between
   every letter.

Part 1 is done. You don't need to repeat it unless you delete the
`venvs\ee475-cnn` folder.

---

## Part 2: Put the firmware on the board (real recordings only)

Skip this part if you only want to use the simulated device.

The board needs firmware-bench version **0.1.4-bench** or later. There are no
settings to change: this version always sends every reading.

1. Open the Arduino IDE. Choose **File > Open** and open
   `firmware\firmware-bench\firmware-bench.ino` inside the project folder.

2. In the **Tools** menu, check these settings:
   - Board: **ESP32S3 Dev Module**
   - USB CDC On Boot: **Enabled**
   - USB Mode: **Hardware CDC and JTAG**
   - Flash Size: **8MB (64Mb)**
   - PSRAM: **Disabled**
   - Upload Mode: **UART0 / Hardware CDC**

3. Plug the USB cable into the board's connector labeled **USB** (not the one
   labeled UART), and the other end into the laptop. In **Tools > Port**, choose
   the board's port.

4. Click the **Upload** button: the round button with an arrow pointing right,
   at the top left. Wait for the message **Done uploading**. This takes about a
   minute.

5. **Close the Arduino IDE completely.** Only one program at a time can use the
   board's connection. If the Arduino IDE, especially its Serial Monitor, is
   still open, the record program can't connect.

---

## Part 3: Start the program

Do this every time you want to use the program.

1. Open Command Prompt or PowerShell (Part 1, step 1).
2. Go to the program's folder (Part 1, steps 3 and 4).
3. Type:

   Command Prompt:

   ```
   "%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" main.py
   ```

   PowerShell:

   ```
   & "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" main.py
   ```

   After a few seconds, a window titled
   **CNN Record (firmware-bench, manual clips)** opens.

Leave the command window open while you work. Closing it also closes the
program.

---

## Part 4: What's on the screen

**Top bar** (one line across the top): the session's name and how many clips it
has of each label, then how many long NONE clips and how many minutes they
add up to. If the session holds older takes from before this version, their
number is shown too. The bar counts the files on disk, so it follows your
relabels and deletes.

**Left column, top: Record.**

- **Port**, **Refresh**, **Simulated device** and **Connect**: choose the board
  (or the simulated device) and connect.
- The **connection line**: a dot (green when connected, amber when the
  connection was lost and the program is retrying) and a message.
- The **settings line** (gray): the board's firmware version, rate, ranges,
  filter and settle time, which chip axis is used for vertical, roll and pitch,
  then how many readings per second arrive and how many the board had to drop.
- **Session**: the session's name, for example `2026-11-23_bench`. Use letters,
  digits, `_` and `-`. Press Enter after changing it.
- **P Pothole**, **B Speed bump**, **N Nothing**: the big record buttons. The
  keys P, B and N do the same.
- **L Long NONE**: starts a long NONE recording; while it runs, the button
  shows the time and stops it.
- **Delete last clip**: moves the last clip you saved to the session's
  `_deleted` folder. The Backspace key does the same.
- With **Simulated device** ticked, a row of test controls (Part 5).

**Left column, middle: Last clip.** A big card you can read from across the
car:

- **READY** (gray): connected; press a key to record.
- **RECORDING POTHOLE** (blue), with the seconds left. You hear a short tone
  when you press.
- The clip's name, for example **POTHOLE-7** (green), when it is saved, with
  its number of rows and its facts. You hear a two-note tone.
- **LONG RECORDING** (blue), with the time so far.
- **FAULT** (red) if the board reports a problem.

Under the card: the last five clips, and this run's counts (saved, not saved
because of a disk error, presses that were ignored, deleted).

**Left column, bottom: Collected clips.** The session's clips, newest at the
top. Click one to see it. Long recordings are marked `(long, ...)`. Takes from
before this version are marked `(older take)`; they still open, relabel and
delete like clips.

**Right column, top: Live IMU.** The board's readings as they arrive (every 8th
one), so you can see the sensor working. It is only for watching; clips take
every reading.

**Right column, middle: Clip** and **Clip info.** The selected clip: z_hp (the
vertical shake, in g) on top, roll and pitch rate (in degrees per second)
below, against seconds from the clip's start. The dashed line is your key press
(1 second in). Beside it, plain facts: rows, missing readings, resets, rows the
board sent before it was settled (`ok=0`), the firmware. These facts never
decide anything; they help you decide.

**Right column, bottom: Serial Monitor.** The program's own log messages.

---

## Part 5: Try it with the simulated device

1. Start the program (Part 3).
2. Tick **Simulated device**, then click **Connect**. The card says **READY**
   (and **SIMULATED** in its corner) after about 2 seconds, and Live IMU starts
   moving.
3. Press **P**. You hear a tone and the card counts down from 5 seconds. With the
   test control set to "shape of the key", the simulated board makes a pothole
   1.5 seconds after your press.
4. When the countdown ends you hear a two-note tone, the card shows the clip's
   name, and the clip appears at the top of Collected clips, with the pothole in
   the plot about 2.5 seconds in.
5. Try the test controls:
   - **after ... s**: when the simulated event happens after a press (0 to
     4.5 s).
   - **Gap**: 20 readings get lost on the way (a jump in the reading numbers).
   - **Reset**: the simulated board restarts.
   - **ok=0**: 50 readings arrive marked "not settled".
   - **Disconnect**: the USB connection drops for 2 seconds, then the board
     restarts.

   Press a key, then a control, and look at the clip's facts: the clip is always
   saved, and the facts say what happened.

---

## Part 6: Record on the bench

1. Do Part 2 if the board doesn't have 0.1.4-bench yet.
2. Start the program, choose the board's port and click **Connect**.
3. Set the session, for example `2026-11-23_bench`, and press Enter.
4. **Keep the board still for 2 seconds after connecting** (and after every
   reset): the board measures its gyro offset then. The settings line shows the
   firmware version once the board's settings arrive (within 5 seconds).
5. Press **P**, then tap or tilt the board within the next few seconds. Wait for
   the two-note tone. Do the same with **B** and **N**.
6. To check that clips catch late events, press then tap at different delays,
   from right away to about 4 seconds later.
7. For normal-road data on the bench, press **L**, leave the board still for a few
   minutes, and press **L** again.
8. Look at each clip (Part 8). Keep it, relabel it, or delete it.

---

## Part 7: Record in a car

Only once the board's mounting in the car is final (decision D5): bench and car
clips are kept in separate sessions.

- Use one session per drive, with `car` in its name, for example
  `2026-11-30_car_route1`.
- The passenger presses **P** or **B** as soon as they see the pothole or bump
  coming. Pressing early is fine: the clip runs until 5 seconds after the press.
- Press **N** now and then on normal road, and record a few minutes of **L**
  (long NONE) on each drive.
- A press during a recording is ignored (the card shows the recording still
  counting down), so two events close together give one clip.
- If you pressed the wrong key, press **Backspace** right after the save, or fix
  it later in Collected clips.
- If the cable comes out during a clip, the clip is still saved with whatever
  arrived, and the program reconnects by itself.

---

## Part 8: Look at, relabel or delete a clip

1. Click the clip in **Collected clips**. Its plot and facts appear on the right.
2. To change its label, choose the new label in the dropdown under the list
   (POTHOLE, SPEED_BUMP or NONE) and click **Relabel**. The file gets a new name,
   and its header records the change.
3. To delete it, click **Delete clip** and confirm. It moves to the session's
   `_deleted` folder; nothing is ever destroyed. To bring it back, move the
   file back by hand.

Older takes labeled FALSE_TRIGGER or UNLABELED stay as they are until you
relabel them.

---

## Part 9: Where everything is saved

- **Clips:** `firmware\cnn\data\<session>\<LABEL>-<number>.csv`. Numbers are
  never reused in a session, deleted clips included. The `data` folder is not
  in git; the team shares it another way.
- **Deleted clips:** `firmware\cnn\data\<session>\_deleted\`.
- **A long recording in progress:** a hidden file
  `firmware\cnn\data\<session>\.long-<number>.partial`. It becomes a clip when
  you stop the recording. If the program crashed during a long recording, the
  file stays there and is never deleted by the program; ask the project owner
  to turn it into a clip.
- **The program's log:** `firmware\cnn\logs\record_gui_<date>.log`.

---

## Part 10: Stop the program

Close the window. A clip or long recording that is still running is saved
first, with whatever it has collected.

---

## Tests

There are two kinds of tests:

- the **automatic checks**, which the computer runs by itself in a few seconds;
- tests **MAN-1 to MAN-4**, which you do by hand with the program open.

All of MAN-1 to MAN-4 are **NOT TESTED** so far. Each test says what to do, what
you should see, when it fails, and what to save as proof. Only the project
owner marks a test as passed. The board's own tests (FWS-1 to FWS-4) are in
the handoff document, and the window-cutting tests (WIN-1 to WIN-4) are in
`..\training\README.md`.

To take a screenshot, press Windows+Shift+S and drag a box around what you
want. Then click the preview that pops up in the corner of the screen, and save
it.

### Automatic checks

**What they check:** the program reads the board's lines correctly; every
press gives a clip of the right length with the right facts, even with lost
readings, resets, `ok=0` rows or a stopped stream; presses during a recording
are ignored; long recordings; clip files, older takes, relabel and delete; and
that the simulated device sends what the real board does.

They don't open the window and need no board, but they need the add-ons (numpy).

**What to do:** go to the program's folder (Part 1, steps 1, 3 and 4), then type:

Command Prompt:

```
"%USERPROFILE%\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

PowerShell:

```
& "$env:USERPROFILE\venvs\ee475-cnn\Scripts\python.exe" -m unittest discover -s tests
```

**What you should see:** a row of dots, then a line starting with
`Ran 62 tests in`, and `OK` on the last line.

**It fails if** the last line says `FAILED` or shows errors. Copy all of the text
from the window and send it to the project owner.

### MAN-1: Build

**Do:** on a computer without the add-on folder, do Part 1 from the start
(including step 7), run the automatic checks, then start the program.

**You should see:** the checks end with `OK`, and the window opens.

**Proof:** the test output, `pip-freeze.txt`, and a screenshot of the window.

### MAN-2: Normal recording

**Do:** first with the simulated device, then with the board. Use a new
session. Press **P** 10 times, **B** 10 times and **N** 10 times, waiting for the
two-note tone each time. With the board, tap it after each press.

**You should see:** 30 clips in the session folder (10 of each label); each one
has 2496 rows (1 second before plus 5 seconds after) and a full header; the
dashed press line is in the plot; with the board, your tap is visible in each
clip; the top bar shows 10, 10 and 10.

**It fails if** a press gave no clip, a clip has the wrong label, or a clip
without problems (no missing readings, no resets) doesn't have 2496 rows.

**Proof:** a listing of the session folder, one clip's header (open it in a text
editor), and screenshots.

### MAN-3: Edge cases

**Do:**

- (a) Press **P** twice within 1 second.
- (b) Hold **P** down for 2 seconds.
- (c) Press **L**, wait 30 seconds, pressing **B** during that time, then press **L**
  again.
- (d) Press **Backspace** right after a clip is saved.
- (e) Relabel one clip and delete another in Collected clips.

**You should see:** (a) and (b) each give one clip; (c) gives one NONE clip of
about 30 seconds (marked long) and no SPEED_BUMP clip; (d) moves the clip to
`_deleted`; (e) changes only the clips you chose.

**Proof:** folder listings before and after, and screenshots.

### MAN-4: Damaged data

**Do:**

- (a) Unplug the USB cable during a clip's countdown, then plug it back in.
- (b) Press the board's **RST** button during a clip's countdown.
- (c) With the simulated device, press a key and then click **ok=0** (also try
  **Gap**, **Reset** and **Disconnect**).

**You should see:** every press still saves a clip; its info panel and header
show the missing readings, the reset or the `ok=0` rows; nothing is deleted or
hidden; the program stays open and reconnects by itself.

**Proof:** the clips, their headers, the program's log, and screenshots.

---

## If something goes wrong

- **It says `'python' is not recognized` or `Python was not found`.**
  Python isn't installed, or it wasn't added to PATH. See Part 1, step 2.

- **PowerShell says `Unexpected token '-m'` (or `'main.py'`).** The `&` at the
  start of the command is missing. Use the PowerShell version of the command.

- **It says `The system cannot find the path specified`, or
  `...\ee475-cnn\Scripts\python.exe` is not recognized.** Part 1 isn't done, or
  a Command Prompt command (with `%USERPROFILE%`) was typed into PowerShell.
  Use the version for your window, and check that the folder `venvs\ee475-cnn`
  exists in your user folder. If it says it can't open `main.py`, the window
  isn't in the program's folder: repeat Part 1, steps 3 and 4.

- **A folder named `%USERPROFILE%` appeared.** Part 1, step 5 was typed into
  PowerShell in its Command Prompt form. Delete that folder in File Explorer and
  do step 5 again with the PowerShell command.

- **It says `No module named PyQt5` (or pyqtgraph, numpy, or serial).** The
  add-ons aren't installed in the program's private folder. Repeat Part 1,
  step 6, and start the program with the command in Part 3.

- **The board isn't in the Port list.** Click **Refresh**. Check that the cable
  is in the board's **USB** connector and that it's a data cable. Close the
  Arduino IDE.

- **The record buttons are grayed out.** Point at one to see why: the program
  must be connected, and the session name must be valid (press Enter after
  typing it).

- **Live IMU stays empty and says "waiting for L lines".** The board isn't
  sending readings. If the settings line says **this firmware sends no L
  lines**, the board has older firmware: do Part 2. Otherwise press **RST** on
  the board.

- **Pressing P, B, N or L typed a letter into a box.** A text box had the
  keyboard. Click somewhere else in the window (or press Enter in the box), then
  press the key again.

- **I pressed a key and nothing happened.** A clip or long recording was
  already running; the press was ignored on purpose and counted under "ignored
  presses".

- **No sound.** The sound comes from the laptop's default speaker; check its
  volume. If the computer can't play the tones, it plays the Windows "beep"
  instead.

- **The card says NOT SAVED.** The clip could not be written to disk (for
  example, the disk is full). The red message line says why.

- **The card says FAULT.** The board reported a problem; the Serial Monitor
  also shows it. Write the code down for the project owner. The card clears the
  next time you click **Connect**.

- **drops in the settings line keeps going up, or clips show missing
  readings.** The laptop isn't reading fast enough, or the cable is poor. Close
  other programs, use another USB socket, and tell the project owner.

- **The window closed unexpectedly.** Start it again (Part 3). Clips that were
  already saved are safe, and the log file says what happened.

---

## For developers

**What changed** (manual-recording-overhaul-handoff.md, request B, owner
decisions O1 to O8). Threshold snapshots, Arm, EVT matching, the drive log,
MARK lines, the window cutter and the `manual/`, `weak/` and `background/`
folders are gone, and so is the request-3 training code with its Training tab
(owner, 2026-11-23). All of it is in git, tag `pre-manual-overhaul`.

**Board lines** (firmware-bench 0.1.4-bench). `L,n,t_us,ax,ay,az,gx,gy,gz,z_hp,roll,pitch,ok`
for every sample (4 decimals; raw chip axes with the gyro bias in, then the
model channels with the bias removed; `ok` is 1 once the bias is ready), plus
`BOOT` / `CFG` (settings, CFG every 5 s), `HLT` (10 fields), `FLT`, `SEG` and
`BLE`. `snapshot_protocol.py` parses them; anything else (including EVT, S and
snapshot lines from older firmware) is ignored. A CFG is not a reboot: it is
used only while the connection has no BOOT (`RecordController.on_settings()`),
stored as the BOOT line it repeats.

**Clip file** (`clip_file.py`, format `clip_v1`). `<LABEL>-<n>.csv` in the
session folder:

- `# key=value` header: `format`, `source` (`manual` or `long`), `label`,
  `session`, `press_epoch_ms`, `press_pc`, `press_n` (the last n received at the
  press), `press_row` (its row in the file), `stop_epoch_ms` and `stop_pc` (long
  only), `first_n`, `last_n`, `pre_s`, `post_s` (long: its length), `rate_hz`,
  `rows`, `missing_samples` (the sum of the jumps in n), `resets` (times n did
  not go up), `ok0_rows`, `boot` (the connection's BOOT or CFG line, or
  `missing (...)`), `gui_commit`, `columns`. A relabel adds `relabeled=`.
- Data rows: the L line's 12 fields without the tag, exactly as received.

Older takes (`take_format=1`, 624 rows of `z_hp,roll,pitch`) are read by the
same `load_clip()`, as kind `take`, with the trigger row as the press row.

**Recording** (`clip_recorder.py`, no Qt). It keeps the last `MANUAL_PRE_S` of
L samples. A press copies them (the newest is the press sample) and collects
`MANUAL_POST_S` more device samples: a jump in n uses up the samples it
skipped, and after a reset each row counts as one. The clip ends when that
count is reached, or `MANUAL_SAVE_GRACE_S` after the countdown if the stream
stopped (`tick()`), or at Disconnect or close (`finish_all()`), and is always
saved. Presses during a recording are ignored. A long recording writes each
batch to `<session>/.long-<epoch_ms>.partial` and is copied into the clip file
on stop (`TakeStore.write_take_streamed()`), so its length has no limit.

**Sounds** (`sounds.py`). Two WAV tones made at start-up with the standard
library into `%TEMP%\cnn_record_gui_sounds\`, played with `QSoundEffect`
(QtMultimedia, part of the installed PyQt5); `QApplication.beep()` if that is
unavailable.

**Simulated device** (`sim_source.py`). Streams what the board does, at 416
L lines per second of device time. Test controls go from the GUI thread
through `SerialHandler.sim_command()` to the reader thread, which hands them to
the device before its next sample.

**Look.** The dark look of Jake's ml-on-device-playground, forced on every OS by
`apply_theme()` in `app_styles.py`. Every color, font size and spacing is a
named token there; widgets get a style role with `set_role()`. The big record
buttons use the `record-key` role.

**Code.**

| File | What it does |
| --- | --- |
| `main.py` | Starts the program; sets up the log, the error hook and the dark theme |
| `app_styles.py` | The look: tokens, the style sheet, the dark palette and title bar |
| `main_window.py` | The window, the top bar, the layout and its splitters |
| `record_widget.py` | The Record panel: connection, session, record keys, simulator controls |
| `record_core.py` | Session, board settings, saving clips, delete last, session counts (no Qt) |
| `clip_recorder.py` | The rolling buffer, clips and long recordings (no Qt) |
| `clip_file.py` | The clip file format: header, facts, `load_clip()`, info lines (numpy, no Qt) |
| `take_store.py` | Clip files on disk: atomic writes, numbering, relabel, delete (standard library) |
| `serial_handler.py` | The reader thread: port or simulated device, parsing, batches, reconnecting |
| `snapshot_protocol.py` | Parses the board's lines (standard library) |
| `sim_source.py` | The simulated device and its test controls (standard library) |
| `sounds.py` | The press and saved tones |
| `status_card.py` | The Last clip card |
| `collected_data_widget.py` | Collected clips: list, view, relabel, delete |
| `snapshot_plot.py` | The clip plot and the clip info panel |
| `live_plot.py`, `live_buffer.py` | The Live IMU view |
| `serial_monitor.py`, `elided_label.py` | The log panel; a label that shortens long text |
| `tests/` | The automatic checks |

All settings are in `..\cnn_config.py`, each with its status (DECIDED or DRAFT).
