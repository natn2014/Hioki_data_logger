# Hioki Data Logger

A Python GUI application that reads resistance measurements from HIOKI multimeters via USB/RS-232 serial and automatically uploads results to a Microsoft SQL Server database. Designed for manufacturing quality control environments.

## Features

- Auto-detects HIOKI devices on available COM ports
- Real-time resistance measurement polling (500 ms interval)
- Per-model pass/fail limit configuration
- Local CSV logging (daily files, no data loss on DB failure)
- Background database uploads with persistent retry queue
- Automatic reconnection with exponential backoff on serial errors
- Process watchdog that kills and restarts a hung app
- Deployable as systemd services on Raspberry Pi 5

## Requirements

**Python 3** with the following packages:

```
PySide2
pyserial
pyodbc
```

**Database:** Microsoft SQL Server with ODBC Driver 18 for SQL Server installed.

**Hardware:** HIOKI resistance meter with SCPI support connected via USB or RS-232.
Tested models: RM3544-01, RM3545, RM3542, DM7276, DM7275, IM7580A.

## Setup

### 1. Install dependencies

```bash
pip install PySide2 pyserial pyodbc
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

The application starts full-screen, auto-detects the connected HIOKI device, and begins polling once a device is found. Click **Model** to enter a product model name before recording measurements.

## Project Structure

```
├── main.py                          # Main GUI application (PySide2)
├── usb_rs.py                        # Serial communication wrapper
├── insert_resistance2db.py          # MSSQL database insertion
├── db_upload_manager.py             # Background upload queue with retry
├── ui_UI_Resistance.py              # Generated Qt UI code
├── UI_Resistance.ui                 # Qt Designer UI definition
├── gui_mode5_config.json            # Per-model pass/fail limits
│
├── setup_services.sh                # Raspberry Pi: install systemd services
├── hioki-app.service                # Systemd unit — main app
├── hioki-watchdog.service           # Systemd unit — watchdog
├── watchdog.sh                      # Watchdog: kills hung process after 30 s in D-state
└── cmd_service_install_watchdog.txt # Quick manual service install reference
```

## Raspberry Pi 5 Deployment

Copy the project to `/home/pi/Hioki_data_logger/` then run the setup script once:

```bash
sudo ./setup_services.sh
```

This installs and enables two systemd services:

| Service | Role |
|---|---|
| `hioki-app.service` | Runs `main.py`, auto-restarts on crash |
| `hioki-watchdog.service` | Kills the app if it enters uninterruptible (D) sleep for 30 s |

Useful commands:

```bash
sudo systemctl status  hioki-app.service
sudo systemctl restart hioki-app.service
sudo systemctl stop    hioki-app.service
journalctl -u hioki-app.service -f
tail -f watchdog.log
```

## Data Logging

- **CSV:** A new `YYYYMMDD.csv` file is created each day in the app directory.
  Columns: `Timestamp`, `Resistance`, `Status` (OK / NG / N/A), `Model`, `Date`, `Time`, `DB_Status`.
- **Database:** Uploads happen in a background thread (5 s timeout). Failed uploads are queued in `pending_uploads.json` and retried every 10 seconds. The queue persists across app restarts.

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
