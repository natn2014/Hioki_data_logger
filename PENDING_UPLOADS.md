# Pending Uploads & Retry Mechanism

## Overview
This application now includes an intelligent upload queue system that prevents GUI hangs when the network disconnects during database uploads.

## How It Works

### **Upload Flow**
1. When a stable reading is detected, the value is **immediately saved to CSV** (local file)
2. The value is then **queued for database upload in a background thread** (non-blocking)
3. If the upload succeeds, it's removed from the queue
4. If the upload fails (network error, server unreachable), the record is **saved to `pending_uploads.json`** for retry

### **Retry Mechanism**
- Every 10 seconds (while connected), the app attempts to upload all pending records
- Retry happens **only when device is connected** (doesn't waste retries on disconnected state)
- Each pending record tracks:
  - Model, value, status
  - Timestamp of original measurement
  - Retry count
  - Upload status

### **Key Features**
✓ **Non-blocking uploads**: Database operations run in background threads  
✓ **Persistent queue**: Pending uploads saved to `pending_uploads.json` (survives app restart)  
✓ **Exponential safety**: Retries don't spam the server  
✓ **Automatic recovery**: Resumes uploading as soon as network reconnects  
✓ **Status tracking**: CSV now includes "DB_Status" column (pending/uploaded)  
✓ **No data loss**: All values stored locally in CSV before attempting DB upload  

## Files Created

### `db_upload_manager.py`
Manages all background uploads with queue and retry logic:
- `upload_async()` - Non-blocking upload to database
- `retry_pending_uploads()` - Batch retry of all pending records
- `add_pending_upload()` - Add record to retry queue
- `load_pending_uploads()` - Recover previous session's pending uploads on startup
- `get_pending_count()` - Check how many records await upload

### `pending_uploads.json`
Stores records failed to upload:
```json
[
  {
    "model": "1SRG14R(BRK)-MM-4FIMXA-A7",
    "value": 102.345,
    "status": "OK",
    "timestamp": "2026-03-19T14:23:45.123456",
    "upload_status": "pending",
    "retry_count": 2
  }
]
```

## CSV Changes

### Before
```
Timestamp,Resistance,Status,Model,Date,Time
2026-03-19 14:23:45,102.345,OK,1SRG14R(BRK)-MM-4FIMXA-A7,2026-03-19,14:23:45
```

### After
```
Timestamp,Resistance,Status,Model,Date,Time,DB_Status
2026-03-19 14:23:45,102.345,OK,1SRG14R(BRK)-MM-4FIMXA-A7,2026-03-19,14:23:45,pending
```

## GUI Indicators

The status log shows:
- `(DB: Uploading...)` - Attempting initial upload
- `! Upload failed - X queued for retry` - Failed, added to retry queue  
- `! X value(s) waiting to upload` - Startup message if pending records exist
- `Retry: X✓ Y✗ (Z pending)` - Result of retry batch

## Scenarios

### Scenario 1: Normal Network
1. Reading taken → CSV saved → DB upload succeeds → removed from queue ✓

### Scenario 2: Network Disconnects Mid-Upload
1. Reading taken → CSV saved → DB upload fails → added to `pending_uploads.json`
2. User moves to area with WiFi
3. App automatically retries → DB upload succeeds → removed from queue ✓

### Scenario 3: App Restart (Network Still Down)
1. App restarts
2. Loads `pending_uploads.json` on startup
3. Shows "! 5 value(s) waiting to upload" message
4. When network recovers → automatically retries all 5 records

### Scenario 4: Server Down (Transient)
1. Multiple reads fail to upload → all queued in `pending_uploads.json`
2. Server comes back online
3. Automatic retry batch uploads all pending records
4. `pending_uploads.json` clears as records succeed

## Cleanup

To manually clear all pending uploads (⚠️ permanent deletion):
```python
db_manager.clear_all_pending()
```

Or delete `pending_uploads.json` directly.

## Database Changes

The `insert_to_mssql()` function in `insert_resistance2db.py` remains **unchanged** - it's called the same way, just from a background thread now.

## Logging

All upload activity is logged to console with `[DBUploadManager]` prefix:
```
[DBUploadManager] Loaded 3 pending uploads from file
[DBUploadManager] Attempting upload: 1SRG14R... = 102.345 (OK)
[DBUploadManager] Retrying upload [1/3]: 1SRG14R... = 102.345
[DBUploadManager] Retry successful: 1SRG14R...
[DBUploadManager] Retry complete: 2 succeeded, 1 failed, 1 remaining
```

## Configuration

Both timers are configurable in `main.py`:
- **Health check**: `5000` ms (5 seconds)
- **Retry uploads**: `10000` ms (10 seconds)

Modify in `start_mode()` method if needed.
