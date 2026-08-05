# Hioki Data Logger

A Python GUI application that reads resistance measurements from HIOKI multimeters via USB/RS-232 serial, judges each reading against per-model pass/fail limits, and automatically uploads results to a Microsoft SQL Server database. Designed for manufacturing quality-control kiosks — including a Raspberry Pi 5 touchscreen deployment.

## Features

- Auto-detects HIOKI devices on available COM ports
- Real-time resistance measurement polling (500 ms interval)
- **Multi-point spec sequences** — a model can define an ordered list of measurement points (e.g. `A-B = 1–3 Ω`, `B-C = 4–7 Ω`, `D-E = 4–9 Ω`); the app judges each reading against the *current* point's range, records which point it was, then **auto-advances** to the next point and wraps back to point 1 after the last
- **In-app model registration** — a touchscreen dialog registers a new model or edits an existing spec; on confirm it writes the point sequence to the `resistance_spec` table
- **Point cards** — tappable cards let the operator jump directly to any point in the sequence (touch-friendly alternative to Prev/Next)
- **On-screen keyboard & numpad** — full QWERTY keyboard (with a Shift toggle) for model/point names and a numeric keypad for limits; both accept USB barcode-scanner input
- **Audio feedback** — plays a Thai-language voice alert on every PASS (`ResistancePass_TH.mp3`) or FAIL (`ResistanceOver_TH.mp3`)
- **Barcode scanner input** — USB HID scanner auto-sets the active model; all entry methods share the same unified decode logic (AIM Code 39 Extended, `$`-delimited, and plain text)
- **Model change log** (`model_changes.csv`) — audit trail of every model switch; used to auto-recover the last model after a crash or reboot
- Local CSV logging (daily files, no data loss on DB failure)
- **Offline-resilient uploads** — measurement rows queue to `pending_uploads.json` and spec writes queue to `pending_specs.csv` when the server is unreachable; both flush automatically with exponential backoff
- **Upload Now button** — force-flush the pending queue to the DB without waiting for the retry timer
- **Startup schema guard** — on launch the app checks the DB is reachable and warns if the multi-point columns are missing (i.e. migration not yet applied)
- Automatic reconnection with exponential backoff on serial errors
- Process watchdog that kills and restarts a hung app
- Deployable as systemd services on Raspberry Pi, with automatic landscape touchscreen rotation

## Requirements

### Python

**Python 3.9+** with PySide6:

```
PySide6       # includes QtMultimedia (QMediaPlayer) via pyside6-addons
pyserial      # provides serial.tools.list_ports
pyodbc
watchdog
requests
```

> Install via `pip install -r requirements.txt`  
> On Raspberry Pi use the setup script — it installs system packages first (see [Raspberry Pi Deployment](#raspberry-pi-deployment)).

### System (Raspberry Pi / Debian)

```bash
sudo apt install python3-serial python3-venv python3-pip \
                 unixodbc-dev libglib2.0-0 libdbus-1-3 libxcb-cursor-dev \
                 gstreamer1.0-plugins-base gstreamer1.0-plugins-good \
                 gstreamer1.0-alsa gstreamer1.0-libav \
                 x11-xserver-utils xinput
```

> The GStreamer packages are required for `QMediaPlayer` to decode and play MP3 files on Linux.  
> `x11-xserver-utils` (xrandr) and `xinput` are used to rotate the touchscreen to landscape at startup.

### Database

Microsoft SQL Server with **ODBC Driver 18 for SQL Server** installed.

### Hardware

HIOKI resistance meter with SCPI support connected via USB or RS-232.  
Tested models: RM3544-01, RM3545, RM3542, DM7276, DM7275, IM7580A.

Touchscreen deployment tested on a Raspberry Pi 5 with an ED-HMI3010-101C 10.1" 1280×800 panel.

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure database connection

Edit [insert_resistance2db.py](insert_resistance2db.py) and update the connection parameters:

```python
server   = '172.18.72.16'     # SQL Server IP or hostname
database = 'ENGINEER_DB'
username = 'engineering_user'
password = 'Engineering@user'
```

### 3. Apply the database migration

Run the migration once against `ENGINEER_DB` to create the spec table and add the
per-reading traceability columns. Both scripts are re-runnable (guarded existence checks).

```bash
# Measurement table (must exist first) + resistance_spec master table
sqlcmd -S 172.18.72.16 -d ENGINEER_DB -U engineering_user -P 'Engineering@user' \
       -i migrations/001_multipoint_spec.sql

# Optional: seed the 500D example sequence (A-B / B-C / D-E)
sqlcmd -S 172.18.72.16 -d ENGINEER_DB -U engineering_user -P 'Engineering@user' \
       -i migrations/002_seed_500D.sql
```

Resulting schema:

```sql
-- Master spec: one row per measurement point of a model's sequence
CREATE TABLE resistance_spec (
    Model      VARCHAR(100) NOT NULL,
    Seq        INT          NOT NULL,   -- 1-based order in the sequence
    PointName  VARCHAR(50)  NOT NULL,   -- e.g. 'A-B'
    LowerLimit FLOAT        NOT NULL,
    UpperLimit FLOAT        NOT NULL,
    Active     BIT          NOT NULL DEFAULT 1,
    UpdatedAt  DATETIME     NOT NULL DEFAULT getdate(),
    CONSTRAINT PK_resistance_spec PRIMARY KEY (Model, Seq)
);

-- Measurement table gains a nullable per-reading snapshot
-- (Point, Seq, LowerLimit, UpperLimit) so historical OK/NG stays explainable
-- even after a spec is later edited.
CREATE TABLE resistance (
    Timestamp  DATETIME DEFAULT GETDATE(),
    Resistance FLOAT,
    Status     NVARCHAR(10),   -- 'OK', 'NG', or 'N/A'
    Model      NVARCHAR(100),
    [Date]     DATE,
    [Time]     TIME,
    Point      VARCHAR(50) NULL,
    Seq        INT         NULL,
    LowerLimit FLOAT       NULL,
    UpperLimit FLOAT       NULL
);
```

> A model with **no** point sequence still works: it is judged against the single
> `lower_limit`/`upper_limit` in `gui_mode5_config.json` and inserts `NULL` point columns.

### 4. Configure measurement limits

Single-range limits are stored in `gui_mode5_config.json` and managed at runtime via the
**Model** button. Multi-point specs are managed via the **+ New Model** / **Edit Spec**
dialog and cached locally per model. To pre-set a single-range limit manually:

```json
{
  "current_model": "MODEL_A",
  "models": {
    "MODEL_A": { "lower_limit": 0.01, "upper_limit": 14.0 }
  }
}
```

## Running

```bash
python main.py
```

The application starts full-screen, auto-detects the connected HIOKI device, and begins polling once a device is found.

## Testing Audio Feedback (Without a Device)

Two keyboard shortcuts let you test the PASS/FAIL sounds without a connected HIOKI meter:

| Shortcut | Effect |
|---|---|
| **Ctrl+P** | Simulate PASS — green button + plays `ResistancePass_TH.mp3` |
| **Ctrl+F** | Simulate FAIL — red button + plays `ResistanceOver_TH.mp3` |

Both shortcuts work at any time while the application is running.

## Setting the Model

Three ways to set the active product model:

| Method | How |
|---|---|
| **Barcode scanner** | Point a USB HID scanner at the product label — decoded and applied instantly |
| **Model button** | Click the large **Model** button and type or paste any supported format (plain name, `$`-delimited raw, or AIM Code 39 raw) |
| **Auto-prompt** | If no model is set when a stable reading is recorded, the app prompts automatically |

The last used model is saved to `gui_mode5_config.json` and restored on the next startup. If that file is missing or empty, the app recovers the model from `model_changes.csv`.

### Barcode Decode Logic

All model-entry paths (scanner, Model button, auto-prompt, and the registration dialog's
name fields) run through the same unified decode function in the order below:

| Priority | Format | Detection | Example input | Result |
|---|---|---|---|---|
| 1 | **Dollar-delimited** | contains `$` | `FOD123$1SRG14R(BRK)-MM$15` | `SRG14R(BRK)-MM` |
| 2 | **AIM Code 39 Extended** | contains `/[A-Z]` escape pair | `?H60AGV/HFCWR/I-MM-4FHX-B5/D...` | `H60AGV(FCWR)-MM-4FHX-B5` |
| 3 | **Plain text** | no `$` or `/X` patterns | `SRG14R-MM-4FHX-B5` | `SRG14R-MM-4FHX-B5` |

#### AIM Code 39 Extended details

1. Strips the leading check-digit character
2. Converts `/H` → `(`, `/I` → `)`, `/L` → `/`, etc. (see full table in [barcodereader.md](barcodereader.md))
3. Stops at `/D` (field separator)

This means you can scan a label, or paste the raw barcode string into the Model button dialog — both produce the same decoded model name.

## Multi-Point Spec Sequences

A model may define an ordered sequence of measurement points, each with its own range.
Example for model `500D`:

| Seq | Point | Lower | Upper |
|-----|-------|-------|-------|
| 1 | A-B | 1 Ω | 3 Ω |
| 2 | B-C | 4 Ω | 7 Ω |
| 3 | D-E | 4 Ω | 9 Ω |

**During measurement**, the app judges each stable reading against the current point's
range, writes a row with `Point`/`Seq`/`LowerLimit`/`UpperLimit`, then auto-advances to the
next point — wrapping back to point 1 after the last. The operator can also tap a **point
card** to jump directly to any point, or reset to point 1.

**Registering / editing a spec** — the **+ New Model** button (or **Edit Spec** for the
current model) opens a touchscreen dialog. Enter the model name and each point's name and
limits (limits use 2 decimals), then confirm:

- If the server is reachable, the sequence is written to `resistance_spec` (an atomic
  delete-then-insert of that model's rows) and cached locally.
- If the server is unreachable, the spec is queued to `pending_specs.csv` and flushed later,
  the same way measurement uploads are queued.

Specs are cached per model in `gui_mode5_config.json` under `models[model]["points"]`, so
the device keeps judging correctly offline.

## Uploads & Offline Resilience

Every stable reading is logged to the daily CSV immediately, then handed to the background
upload manager. If the DB is unreachable the record is kept safe and retried:

| Queue | File | Contents |
|---|---|---|
| Measurements | `pending_uploads.json` | Readings awaiting DB insert (survives restarts) |
| Specs | `pending_specs.csv` | Registered/edited specs awaiting DB write |

Retries use exponential backoff. A header banner shows how many readings are waiting, and
the **⬆ Upload Now** button force-flushes both queues immediately without waiting for the
timer. Old-format queue entries (written before the multi-point fields existed) still flush
correctly — missing fields insert as `NULL`.

## Project Structure

```
├── main.py                    # Main GUI application (PySide6)
├── usb_rs.py                  # Serial communication wrapper
├── insert_resistance2db.py    # MSSQL insert + spec read/write + schema check
├── db_upload_manager.py       # Background upload queue + spec queue with retry
├── ui_UI_Resistance.py        # Qt UI code (PySide6)
├── UI_Resistance.ui           # Qt Designer UI definition
├── numpad_dialog.py           # On-screen numeric keypad (limit entry)
├── keyboard_dialog.py         # On-screen QWERTY keyboard (model/point names)
│
├── migrations/
│   ├── 001_multipoint_spec.sql  # Create resistance_spec + extend resistance
│   └── 002_seed_500D.sql        # Seed the 500D example sequence
│
├── ResistancePass_TH.mp3      # Audio alert played on PASS result
├── ResistanceOver_TH.mp3      # Audio alert played on FAIL result
│
├── gui_mode5_config.json      # Per-model limits + point-sequence cache + last model
├── model_changes.csv          # Audit log of every model switch (auto-created)
├── pending_uploads.json       # DB retry queue for readings (auto-created)
├── pending_specs.csv          # DB retry queue for spec writes (auto-created)
├── YYYYMMDD.csv               # Daily measurement log (auto-created)
│
├── HIOKI_UI_mockup.html       # Interactive UI mockup (QC panel + Register Model)
├── barcodereader.md           # Barcode decode specification
├── MSW_update.md              # Multi-point feature change notes
├── setup_services.sh          # Raspberry Pi: install services + touch rotation
├── hioki-app.service          # Systemd unit — main app
├── hioki-watchdog.service     # Systemd unit — watchdog
├── watchdog.sh                # Watchdog: kills hung process after 30 s in D-state
└── requirements.txt           # Python package list
```

## Raspberry Pi Deployment

Copy (or clone) the project to `~/Hioki_data_logger/`, then run the setup script once:

```bash
# Fresh clone (the path must be exactly ~/Hioki_data_logger)
cd ~
git clone -b MSW --single-branch https://github.com/natn2014/Hioki_data_logger.git
cd Hioki_data_logger
rm -rf .venv                    # drop any committed Windows venv before building the Linux one

chmod +x setup_services.sh
sudo ./setup_services.sh
sudo reboot                     # so the touchscreen rotation loads
```

The script will:

1. Install system packages via `apt` (serial, ODBC libs, XCB cursor, xrandr/xinput)
2. Create a `.venv` virtual environment with `--system-site-packages`
3. Install all Python packages from `requirements.txt`
4. **Verify every module imports without error** — exits with a clear failure message if any import fails
5. Configure landscape touchscreen rotation (see below)
6. Write and enable two systemd services
7. Start both services and show their status

### Touchscreen rotation

The panel ships in portrait (kiosk) orientation. The setup script configures a landscape
rotation that survives reboot:

- Writes `/etc/X11/xorg.conf.d/90-hioki-touch-rotate.conf` (an `InputClass` that maps touch
  coordinates for any touchscreen).
- Generates a `rotate-display.sh` that runs as an `ExecStartPre` of `hioki-app.service`. It
  waits (retry loop) until the display output is ready, rotates the screen via `xrandr`,
  applies the touch matrix `0 -1 1 1 0 0 0 0 1` per touchscreen via `xinput`, and verifies
  the result is landscape.

If rotation ever reverts, check:

```bash
journalctl -u hioki-app.service -b | grep rotate-display
```

### Services

| Service | Role |
|---|---|
| `hioki-app.service` | Runs `main.py`, rotates the display, waits for X11, auto-restarts on crash |
| `hioki-watchdog.service` | Kills the app if it enters uninterruptible (D) sleep for 30 s |

### Useful Commands

```bash
sudo systemctl status  hioki-app.service
sudo systemctl restart hioki-app.service
sudo systemctl stop    hioki-app.service
journalctl -u hioki-app.service -f
tail -f ~/Hioki_data_logger/watchdog.log
```

## Data Logging

| File | Contents |
|---|---|
| `YYYYMMDD.csv` | One row per stable reading: `Timestamp`, `Resistance`, `Status`, `Model`, `Date`, `Time`, `Point`, `Seq`, `LowerLimit`, `UpperLimit`, `DB_Status` |
| `model_changes.csv` | One row per model event: `Timestamp`, `Action`, `Previous_Model`, `New_Model`, `Source` |
| `pending_uploads.json` | Reading upload retry queue — survives app restarts |
| `pending_specs.csv` | Spec write retry queue — survives app restarts |

### Model Change Actions

| Action | Meaning |
|---|---|
| `STARTUP` | App started with this model (from config or recovery) |
| `CHANGE` | Model switched — source is `barcode`, `manual`, or `prompt` |
| `RESTORE` | Model recovered from `model_changes.csv` after config was missing |

## Connection Resilience

| Mechanism | Detail |
|---|---|
| Exponential backoff | Reconnect delay: 1 s → 1.5 s → … → 60 s max |
| `*IDN?` heartbeat | Every 30 s to detect silent device failures |
| Consecutive timeout limit | 3 timeouts → reconnect |
| Poll thread crash guard | Unexpected exception emits reconnect signal |
| Upload retry queues | Readings and specs persist to disk and retry with backoff |
| Watchdog (Linux) | Kills app process after 30 s in D-state |

## SCPI Command Reference

See [README_COMMANDS.md](README_COMMANDS.md) for the full list of HIOKI SCPI commands.
For the multi-point feature design notes, see [MSW_update.md](MSW_update.md).
