# Hioki Data Logger

A Python GUI application that reads resistance measurements from HIOKI multimeters via USB/RS-232 serial and automatically uploads results to a Microsoft SQL Server database. Designed for manufacturing quality control environments.

## Features

- Auto-detects HIOKI devices on available COM ports
- Real-time resistance measurement polling (500 ms interval)
- Per-model pass/fail limit configuration with persistent storage
- **Audio feedback** — plays a Thai-language voice alert on every PASS (`ResistancePass_TH.mp3`) or FAIL (`ResistanceOver_TH.mp3`)
- **Barcode scanner input** — USB HID scanner auto-sets the active model; all three entry methods share the same unified decode logic (AIM Code 39 Extended, `$`-delimited, and plain text)
- **Model change log** (`model_changes.csv`) — audit trail of every model switch; used to auto-recover the last model after a crash or reboot
- Local CSV logging (daily files, no data loss on DB failure)
- Background database uploads with persistent retry queue
- Automatic reconnection with exponential backoff on serial errors
- Process watchdog that kills and restarts a hung app
- Deployable as systemd services on Raspberry Pi

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
                 gstreamer1.0-alsa gstreamer1.0-libav
```

> The GStreamer packages are required for `QMediaPlayer` to decode and play MP3 files on Linux.

### Database

Microsoft SQL Server with **ODBC Driver 18 for SQL Server** installed.

### Hardware

HIOKI resistance meter with SCPI support connected via USB or RS-232.  
Tested models: RM3544-01, RM3545, RM3542, DM7276, DM7275, IM7580A.

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

Target table schema:

```sql
CREATE TABLE resistance (
    Timestamp  DATETIME DEFAULT GETDATE(),
    Resistance FLOAT,
    Status     NVARCHAR(10),   -- 'OK', 'NG', or 'N/A'
    Model      NVARCHAR(100),
    [Date]     DATE,
    [Time]     TIME
);
```

### 3. Configure measurement limits

Limits are stored in `gui_mode5_config.json` and managed at runtime via the **Model** button in the GUI. To pre-set them manually:

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

All three model-entry paths (scanner, Model button, auto-prompt) run through the same unified decode function in the order below:

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

## Project Structure

```
├── main.py                    # Main GUI application (PySide6)
├── usb_rs.py                  # Serial communication wrapper
├── insert_resistance2db.py    # MSSQL database insertion
├── db_upload_manager.py       # Background upload queue with retry
├── ui_UI_Resistance.py        # Qt UI code (PySide6)
├── UI_Resistance.ui           # Qt Designer UI definition
├── numpad_dialog.py           # On-screen numeric keypad for touchscreen limit entry
│
├── ResistancePass_TH.mp3      # Audio alert played on PASS result
├── ResistanceOver_TH.mp3      # Audio alert played on FAIL result
│
├── gui_mode5_config.json      # Per-model pass/fail limits + last model
├── model_changes.csv          # Audit log of every model switch (auto-created)
├── pending_uploads.json       # DB retry queue (auto-created)
├── YYYYMMDD.csv               # Daily measurement log (auto-created)
│
├── barcodereader.md           # Barcode decode specification
├── setup_services.sh          # Raspberry Pi: install systemd services
├── hioki-app.service          # Systemd unit — main app
├── hioki-watchdog.service     # Systemd unit — watchdog
├── watchdog.sh                # Watchdog: kills hung process after 30 s in D-state
└── requirements.txt           # Python package list
```

## Raspberry Pi Deployment

Copy the project to `/home/pi/Hioki_data_logger/` then run the setup script once:

```bash
chmod +x setup_services.sh
sudo ./setup_services.sh
```

The script will:

1. Install system packages via `apt` (serial, ODBC libs, XCB cursor)
2. Create a `.venv` virtual environment with `--system-site-packages`
3. Install all Python packages from `requirements.txt`
4. **Verify every module imports without error** — exits with a clear failure message if any import fails
5. Write and enable two systemd services
6. Start both services and show their status

### Services

| Service | Role |
|---|---|
| `hioki-app.service` | Runs `main.py`, waits for X11 display, auto-restarts on crash |
| `hioki-watchdog.service` | Kills the app if it enters uninterruptible (D) sleep for 30 s |

### Useful Commands

```bash
sudo systemctl status  hioki-app.service
sudo systemctl restart hioki-app.service
sudo systemctl stop    hioki-app.service
journalctl -u hioki-app.service -f
tail -f /home/pi/Hioki_data_logger/watchdog.log
```

## Data Logging

| File | Contents |
|---|---|
| `YYYYMMDD.csv` | One row per stable reading: `Timestamp`, `Resistance`, `Status`, `Model`, `Date`, `Time`, `DB_Status` |
| `model_changes.csv` | One row per model event: `Timestamp`, `Action`, `Previous_Model`, `New_Model`, `Source` |
| `pending_uploads.json` | DB upload retry queue — survives app restarts |

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
| Watchdog (Linux) | Kills app process after 30 s in D-state |

## SCPI Command Reference

See [README_COMMANDS.md](README_COMMANDS.md) for the full list of HIOKI SCPI commands.
