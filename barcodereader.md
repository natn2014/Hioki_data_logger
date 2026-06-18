# Barcode Scanner Logic

## Overview

The app reads a barcode via a **USB HID barcode scanner** that types characters directly into an `<input>` field and sends Enter at the end. The raw scanned string is encoded and must be decoded before use.

---

## Trigger / Input Capture

Two listeners on `#scanInput`:

| Method | Detail |
|---|---|
| **Enter key** | Fires immediately on `keydown` when `key === 'Enter'` |
| **Debounce auto-submit** | `input` event resets a 100 ms timer; fires `handleScan` when no new character arrives for 100 ms (handles scanners that don't send Enter) |

After processing, the input is cleared and re-focused so the next scan is ready.

---

## Raw Barcode Format (Code 39 Extended / AIM)

The barcode encodes the **product model number** using **Code 39 Extended encoding** with AIM `/X` escape sequences.

### Structure of the raw string

```
[1 check/flag char][encoded model string][/D][...remaining fields...]
```

| Part | Example | Meaning |
|---|---|---|
| First character | `?` or any char | Check digit / scanner prefix — **always stripped** |
| Encoded model | `H60AGV/HFCWR/I-MM-4FHX-B5` | Model with special chars encoded as `/X` pairs |
| `/D` | `/D` | AIM field separator (comma) — parsing **stops here** |
| Remaining | anything after `/D` | Ignored (other barcode fields, serial, date, etc.) |

---

## AIM `/X` Character Encoding Table

Special characters that cannot be represented natively in Code 39 are encoded as a two-character sequence `/ + letter`:

| Code | Decoded char | Code | Decoded char |
|---|---|---|---|
| `/A` | ` ` (space) | `/N` | `;` |
| `/B` | `!` | `/O` | `<` |
| `/C` | `"` | `/P` | `=` |
| `/D` | `,` **(field separator — stop)** | `/Q` | `>` |
| `/E` | `%` | `/R` | `?` |
| `/F` | `&` | `/S` | `@` |
| `/G` | `'` | `/T` | `[` |
| `/H` | `(` | `/U` | `\` |
| `/I` | `)` | `/V` | `]` |
| `/J` | `*` | `/W` | `^` |
| `/K` | `+` | `/X` | `_` |
| `/L` | `/` | `/Y` | `` ` `` |
| `/M` | `:` | `/Z` | `{` |

---

## Decode Algorithm (`decodeBarcode`)

```js
function decodeBarcode(raw) {
  let s = raw.slice(1);          // Step 1: strip first char (check digit)
  let result = '';
  let i = 0;
  while (i < s.length) {
    if (s[i] === '/' && i + 1 < s.length) {
      const code = s.substring(i, i + 2).toUpperCase();
      if (code === '/D') break;                      // Step 2: stop at field separator
      if (AIM_MAP[code] !== undefined) {
        result += AIM_MAP[code];                     // Step 3: decode /X → special char
        i += 2;
        continue;
      }
    }
    result += s[i];                                  // Step 4: pass regular chars through
    i++;
  }
  return result.trim();
}
```

### Concrete decode example

Barcode prints model `H60AGV(FCWR)-MM-4FHX-B5`.

| Raw segment | Decoded | Note |
|---|---|---|
| `[first char]` | *(stripped)* | check digit |
| `H60AGV` | `H60AGV` | literal |
| `/H` | `(` | AIM map |
| `FCWR` | `FCWR` | literal |
| `/I` | `)` | AIM map |
| `-MM-4FHX-B5` | `-MM-4FHX-B5` | literal |
| `/D` | *(stop)* | field separator |

Result → `H60AGV(FCWR)-MM-4FHX-B5`

---

## Lookup & Action

After decoding, the model string is matched **case-insensitively** against the `MASTER` array:

```js
MASTER.find(m => m.model.toUpperCase() === decoded.toUpperCase())
```

Each entry has three fields:

```js
{ customer: "NISSAN", model: "H60AGV(FCWR)-MM-4FHX-B5", group: 1 }
```

| Field | Usage |
|---|---|
| `model` | Match key from barcode |
| `customer` | Display label (NISSAN / MMTH / ISUZU / etc.) |
| `group` | Maps to image folder `images/G{group}/G{group}_{n}.jpg` |

---

## Post-lookup Behavior

- **Not found** → show error overlay with the decoded model string.
- **Found, same group** → status rows updated, slideshow keeps running (no restart).
- **Found, new group** → slideshow restarts from slide 1 for the new group folder.

Slideshow cycles images every **60 seconds**, auto-detecting the last slide via `onerror` on `<img>` load, then looping back to slide 1.

---

## Adapting to Another Project

To reuse this barcode decode logic elsewhere:

1. **Copy `AIM_MAP` and `decodeBarcode()`** — they are self-contained.
2. **Input capture**: listen for `keydown Enter` + a 100 ms debounce on `input` events.
3. **After decode**: the returned string is the human-readable model/part number — plug into your own lookup or routing logic.
4. **If your barcode has no check-digit prefix**, change `raw.slice(1)` to `raw.slice(0)` (or make the strip count configurable).
5. **If your barcode uses multiple `/D`-separated fields**, replace the `break` on `/D` with a split accumulator to capture each field.
