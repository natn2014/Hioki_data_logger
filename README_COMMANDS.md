# HIOKI Resistance Meter - SCPI Commands Guide

## How to Use Commands with This Program

1. Run `main.py`
2. Select the COM port (where your HIOKI meter is connected)
3. Set the baud rate (default 9600)
4. Enter commands:
   - Commands ending with `?` will request data and display the response
   - Commands without `?` will only send the command

---

## Essential Commands

### Device Identification

| Command | Description |
|---------|-------------|
| `*IDN?` | Get device identification (Model, Serial Number, etc.) |
| `*RST` | Reset the device to default settings |

### Resistance Measurement

| Command | Description |
|---------|-------------|
| `MEAS:RES?` | **Read resistance value (ohms)** ✓ Working |
| `MEAS:RES:RANGE:AUTO ON` | Enable auto-ranging |
| `MEAS:RES:RANGE:AUTO OFF` | Disable auto-ranging |
| `MEAS:RES:RANGE 1000` | Set range to 1kΩ |

### Common Range Settings
- `100` - 100Ω
- `1000` - 1kΩ
- `10000` - 10kΩ
- `100000` - 100kΩ
- `1000000` - 1MΩ
- `10000000` - 10MΩ

### Measurement Mode Selection

| Command | Description |
|---------|-------------|
| `FUNC "RES"` | Set function to Resistance measurement |
| `FUNC?` | Read current measurement function |

### Measurement Rate & Speed

| Command | Description |
|---------|-------------|
| `RATE FAST` | Fast measurement rate |
| `RATE MED` | Medium measurement rate |
| `RATE SLOW` | Slow measurement rate (more accurate) |
| `RATE?` | Query current rate |

### Temperature Compensation

| Command | Description |
|---------|-------------|
| `TEMP:REF <value>` | Set reference temperature |
| `TEMP:COMP ON` | Enable temperature compensation |
| `TEMP:COMP OFF` | Disable temperature compensation |

### Data Acquisition

| Command | Description |
|---------|-------------|
| `FETC?` | Fetch the last measured value |
| `DATA:LAST?` | Get the last measured data point |
| `DATA:STAT?` | Get statistics of measurements |

### Specification Limits (Pass/Fail Testing)

**For HIOKI RM3544-01:**

| Command | Description |
|---------|-------------|
| `JUDGE:LEVEL:UPPER <value>` | Set upper specification limit |
| `JUDGE:LEVEL:LOWER <value>` | Set lower specification limit |
| `JUDGE:STATE ON` | Enable judge (pass/fail) checking |
| `JUDGE:STATE OFF` | Disable judge checking |
| `JUDGE:RESULT?` | Check pass/fail result (0=PASS, 1=FAIL) |

**Note:** Use `JUDGE` commands for RM3544-01 model. Other HIOKI models may use `LIMIT` commands.

### Output/Display Control

| Command | Description |
|---------|-------------|
| `DISP ON` | Turn display ON |
| `DISP OFF` | Turn display OFF |
| `DISP:TEXT "<message>"` | Display text message on screen |

---

## Example Usage Scenarios

### Scenario 1: Basic Resistance Reading
```
*IDN?                    (Check device connection)
MEAS:RES?               (Read resistance value)
```

### Scenario 2: High-Accuracy Measurement
```
MEAS:RES:RANGE:AUTO ON  (Enable auto-ranging)
RATE SLOW               (Use slow rate for accuracy)
MEAS:RES?               (Read value)
```

### Scenario 3: Specific Range Measurement
```
MEAS:RES:RANGE 1000     (Set to 1kΩ range)
MEAS:RES?               (Read resistance)
```

### Scenario 4: Multiple Readings
```
MEAS:RES?               (Read 1st value)
MEAS:RES?               (Read 2nd value)
MEAS:RES?               (Read 3rd value)
```

### Scenario 5: Pass/Fail Testing with Limits
```
JUDGE:LEVEL:LOWER 100           (Set lower limit to 100Ω)
JUDGE:LEVEL:UPPER 1000          (Set upper limit to 1kΩ)
JUDGE:STATE ON                  (Enable judge checking)
MEAS:RES?                        (Read value - meter will check against limits)
JUDGE:RESULT?                    (Query result: 0=PASS, 1=FAIL)
```

---

## Response Format

- **Success**: The meter returns the measured value
  - Example: `1234.56` (in ohms)

- **Error**: The meter returns an error message
  - Check the command syntax
  - Verify the meter supports the command

---

## Typical Baud Rate Settings

| Model | Baud Rate |
|-------|-----------|
| HIOKI RM3544-01 | 9600 (default) |
| HIOKI RM3545/RM3542 | 9600 (default) |
| HIOKI DM7276 | 9600 |
| HIOKI DM7275 | 9600 |

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| No response | Check baud rate (usually 9600) |
| "Command Error" | Verify command syntax and spacing |
| Port Permission Error | Run Python as Administrator |
| Port Already in Use | Close other serial applications |
| LIMIT commands not working | Your meter model may not support limit commands. Check your meter's manual or contact HIOKI support. |

---

## Getting Help with Your Meter Model

To find the correct commands for your HIOKI meter:

1. **Check your meter's manual** for the supported SCPI commands
2. **Use `*IDN?` command** to identify your exact model:
   - Example response: `HIOKI,RM3545,12345,1.0`
   - Model name is the 2nd value (RM3545 in this example)
3. **Search online** for "[YOUR_MODEL] SCPI commands" or "programming manual"
4. **Alternative syntax** - Some meters use different command structures:
   - Try: `LIMIT:UPP <value>` instead of `LIMIT:HIGH`
   - Try: `LIMIT:LOW <value>` (already standard)
   - Try: `COND:LIMIT:HIGH <value>` or similar variations

---

## Notes

- Most commands are **case-insensitive**
- Use spaces between command and parameters
- Queries (with `?`) always return data
- Set commands don't return data
- Some meters may have different command variations

For your specific HIOKI model, refer to the official manual for the complete command list.
