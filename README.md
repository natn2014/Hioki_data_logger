# Hioki Data Logger

A Python GUI application that reads resistance measurements from HIOKI multimeters via USB/RS-232 serial and automatically uploads results to a Microsoft SQL Server database. Designed for manufacturing quality control environments.

## Features

- Auto-detects HIOKI devices on available COM ports
- Real-time resistance measurement polling (500 ms interval)
- Per-model pass/fail limit configuration
- Local CSV logging (daily files, no data loss on DB failure)
- Background database uploads with persistent retry queue
- Automatic reconnection with exponential backoff
- Deployable as a systemd service on Raspberry Pi 5

## Requirements

**Python 3** with the following packages:

```
PySide2
pyserial
pyodbc
```

**Database:** Microsoft SQL Server with ODBC Driver 18 for SQL Server installed.

**Hardware:** HIOKI resistance meter with SCPI support connected via USB or RS-232 (tested models: RM3544-01, RM3545, RM3542, DM7276, DM7275, IM7580A).

## Setup

### 1. Install dependencies

```bash
pip install PySide2 pyserial pyodbc
```

### 2. Configure database connection

Edit [insert_resistance2db.py](insert_resistance2db.py) and update the connection parameters:



The target table requires these columns: `Timestamp`, `Resistance`, `Status`, `Model`, `Date`, `Time`.

### 3. Configure measurement limits

Edit [config.json](config.json) to define per-model pass/fail thresholds:

```json
{
  "lower_limit": 0.1,
  "upper_limit": 10,
  "current_model": "MODEL_NAME",
  "models": {
    "MODEL_NAME": {
      "lower_limit": 0.01,
      "upper_limit": 14.0
    }
  }
}
```

Limits can also be changed at runtime via the **Model** button in the GUI.

## Running

```bash
python main.py
```

The application auto-detects the connected HIOKI device. Once connected, it polls for stable measurements and logs each result to a daily CSV file (`YYYYMMDD.csv`) and uploads to the database in the background.

## Project Structure

```
├── main.py                    # Main GUI application (PySide2)
├── usb_rs.py                  # Serial communication wrapper
├── insert_resistance2db.py    # MSSQL database insertion
├── db_upload_manager.py       # Background upload queue with retry
├── ui_UI_Resistance.py        # Generated Qt UI code
├── UI_Resistance.ui           # Qt Designer UI definition
├── config.json                # Model limits configuration
├── gui_mode5_config.json      # Alternate mode configuration
├── run_hioki_app.sh           # App launcher (venv-aware)
├── setup_pi_autostart.sh      # Raspberry Pi systemd service installer
└── monitor_network_status.sh  # Network connectivity monitor
```

## Raspberry Pi 5 Deployment

Install as a systemd service that starts automatically on boot:

```bash
sudo ./setup_pi_autostart.sh
```

Check service status:

```bash
systemctl status hioki-app.service
journalctl -u hioki-network-status.service -f
```

The launcher script [run_hioki_app.sh](run_hioki_app.sh) uses the `.venv` virtualenv if present, otherwise falls back to system Python 3.

## Data Logging

- **CSV:** A new file `YYYYMMDD.csv` is created each day. Columns: `Timestamp`, `Resistance`, `Status` (OK/NG/N/A), `Model`, `Date`, `Time`, `DB_Status`.
- **Database:** Uploads happen in a background thread (5 s timeout). Failed uploads are queued to `pending_uploads.json` and retried every 10 seconds. The queue persists across app restarts.

## Connection Resilience

- Port state is verified every 5 seconds.
- A `*IDN?` health-check query runs every 30 seconds to detect silent failures.
- Reconnection uses exponential backoff: 1 s → 1.5 s → 2.25 s … up to 60 s.

## Documentation

| File | Contents |
|---|---|
| [README_COMMANDS.md](README_COMMANDS.md) | HIOKI SCPI command reference |
| [DISCONNECT_FIXES.md](DISCONNECT_FIXES.md) | Connection stability improvements |
| [GUI_HANG_FIX.md](GUI_HANG_FIX.md) | Thread-safe upload system details |
| [PENDING_UPLOADS.md](PENDING_UPLOADS.md) | Retry mechanism details |
| [PI5_AUTOSTART.md](PI5_AUTOSTART.md) | Raspberry Pi 5 autostart setup |
