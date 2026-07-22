# MSW Update — Multi-Point Spec Sequence with Auto-Switching

## Concept

Previously each **model** had a single pass/fail range (`lower_limit` / `upper_limit`).
Real parts have an **ordered sequence of measurement points**, each with its own range.
Example — model `500D`:

| Seq | Point | Lower | Upper |
|-----|-------|-------|-------|
| 1   | A-B   | 1     | 3 Ω   |
| 2   | B-C   | 4     | 7 Ω   |
| 3   | D-E   | 4     | 9 Ω   |

**How it works now:** the operator probes point after point. The app judges each stable
reading against the **current** point's range, records **which** point it was, then
**auto-advances** to the next point — wrapping back to point 1 after the last. Specs live
centrally in the DB and are cached locally so the device keeps measuring when the server
is offline.

```
 probe A-B ──► judge vs 1–3Ω ──► record (Point=A-B) ──► advance
 probe B-C ──► judge vs 4–7Ω ──► record (Point=B-C) ──► advance
 probe D-E ──► judge vs 4–9Ω ──► record (Point=D-E) ──► advance ──► wrap to A-B
```

The point sequence is the **master data** (a new table, one row per point). The **reading**
records a snapshot of the point + limits it was judged against, so a historical OK/NG stays
explainable even after the spec is later edited.

### How many points? (any N ≥ 1)

The point count is **not** hard-coded — the auto-advance is
`current_point_index = (current_point_index + 1) % len(spec_points)`, so the sequence cycles
through however many points the model has and wraps after the last. `500D` above happens to
have 3; the same logic drives 2, 4, 10, or 1.

**A 2-point model** (e.g. `750X` with `A-B` and `C-D`) behaves exactly like the 3-point case,
just a shorter cycle:

```
 probe A-B ──► judge vs A-B range ──► record (Point=A-B, Seq 1) ──► advance
 probe C-D ──► judge vs C-D range ──► record (Point=C-D, Seq 2) ──► advance ──► wrap to A-B
```

- The header reads `Measured — A-B … 1/2`, then `C-D … 2/2`, then back to `1/2`.
- Two tappable point cards are shown; the active one is highlighted.
- Each reading is still tagged with its own `Point / Seq / LowerLimit / UpperLimit`.

**Edge cases:**
- **1 point** — valid. It never advances away (`% 1 == 0`), so every stable reading is judged
  against that single point; the header stays `1/1`.
- **0 points** — the model has no sequence, so it falls back to the legacy single
  `lower_limit` / `upper_limit` range (Point columns written as `NULL`). See the local-cache
  note below.

---

## Data structure

### Table 1 — `resistance_spec` (NEW, master: the point sequence)

One row per measurement point of a model's sequence. This is the source of truth; the
device caches it into `gui_mode5_config.json` for offline use.

| Column       | Type          | Notes                                            |
|--------------|---------------|--------------------------------------------------|
| `Model`      | VARCHAR(100)  | PK part. Model name, e.g. `500D`.                |
| `Seq`        | INT           | PK part. 1-based order in the sequence.          |
| `PointName`  | VARCHAR(50)   | Point label, e.g. `A-B`.                         |
| `LowerLimit` | FLOAT         | Pass range lower bound (Ω).                      |
| `UpperLimit` | FLOAT         | Pass range upper bound (Ω).                      |
| `Active`     | BIT           | `1` = included in the sequence (default `1`).    |
| `UpdatedAt`  | DATETIME      | Last edit time (default `getdate()`).            |

**Primary key:** `(Model, Seq)`

```sql
CREATE TABLE dbo.resistance_spec (
    Model       VARCHAR(100) NOT NULL,
    Seq         INT          NOT NULL,   -- 1-based order in the sequence
    PointName   VARCHAR(50)  NOT NULL,   -- e.g. 'A-B'
    LowerLimit  FLOAT        NOT NULL,
    UpperLimit  FLOAT        NOT NULL,
    Active      BIT          NOT NULL DEFAULT 1,
    UpdatedAt   DATETIME     NOT NULL DEFAULT getdate(),
    CONSTRAINT PK_resistance_spec PRIMARY KEY (Model, Seq)
);
```

> **Why a separate table (not extra columns on `resistance`)?**
> Adding `point1_low, point1_high, point2_low, …` would hard-cap the number of points and
> break normalization. One row per point supports any number of points and lets specs be
> edited centrally without touching measurement data.

Example rows for `500D`:

| Model | Seq | PointName | LowerLimit | UpperLimit | Active |
|-------|-----|-----------|------------|------------|--------|
| 500D  | 1   | A-B       | 1          | 3          | 1      |
| 500D  | 2   | B-C       | 4          | 7          | 1      |
| 500D  | 3   | D-E       | 4          | 9          | 1      |

### Table 2 — `resistance` (EXTENDED, measurements)

Existing measurement table; **four nullable columns added** for per-reading traceability.
`NULL` in these columns means a single-range model (no point sequence) — fully backward
compatible.

| Column        | Type          | Status  | Notes                                         |
|---------------|---------------|---------|-----------------------------------------------|
| `Timestamp`   | DATETIME      | existing| Server insert time (`getdate()`).             |
| `Resistance`  | FLOAT         | existing| Measured value (Ω).                           |
| `Status`      | VARCHAR       | existing| `OK` / `NG` / `N/A`.                           |
| `Model`       | VARCHAR       | existing| Model name.                                   |
| `Date`        | (date/text)   | existing| Local date string.                            |
| `Time`        | (time/text)   | existing| Local time string.                            |
| `Point`       | VARCHAR(50)   | **NEW** | Point this reading belongs to (e.g. `A-B`).   |
| `Seq`         | INT           | **NEW** | Point's sequence index.                       |
| `LowerLimit`  | FLOAT         | **NEW** | Snapshot of the range used to judge.          |
| `UpperLimit`  | FLOAT         | **NEW** | Snapshot of the range used to judge.          |

```sql
ALTER TABLE dbo.resistance ADD Point VARCHAR(50) NULL;
ALTER TABLE dbo.resistance ADD Seq INT NULL;
ALTER TABLE dbo.resistance ADD LowerLimit FLOAT NULL;
ALTER TABLE dbo.resistance ADD UpperLimit FLOAT NULL;
```

### Relationship

```
resistance_spec (Model, Seq)  ──1 : many──►  resistance (Model, Seq, Point, LowerLimit, UpperLimit)
        master definition                        snapshot copied onto each reading
```

`resistance` stores a **copy** of the point's limits at measurement time (not a foreign
key), so editing a spec later never rewrites the meaning of past results.

---

## Local cache (`gui_mode5_config.json`)

The fetched sequence is cached per model so measurement continues offline:

```json
{
  "current_model": "500D",
  "models": {
    "500D": {
      "lower_limit": 1.0,
      "upper_limit": 3.0,
      "points": [
        { "seq": 1, "name": "A-B", "lower": 1.0, "upper": 3.0 },
        { "seq": 2, "name": "B-C", "lower": 4.0, "upper": 7.0 },
        { "seq": 3, "name": "D-E", "lower": 4.0, "upper": 9.0 }
      ]
    }
  }
}
```

A model with no `points` array falls back to the legacy single `lower_limit` / `upper_limit`
range.

---

## Registering / editing a model spec (in-app)

Operators create or edit a model's point sequence from the QC screen — no direct SQL needed.

- **⚙ Edit Spec** button (next to Prev/Reset/Next) opens `ModelSpecDialog`, prefilled with the
  current model and its points.
- The dialog is a touch-friendly editor: model-name field + one row per point
  (`Seq · PointName · Lower · Upper · remove`), an **＋ Add point** button, and the numeric
  limits open the on-screen `NumpadDialog` on tap. Seq auto-renumbers.
- The **model-name and point-name fields accept the barcode scanner**: scanned input is
  cleansed with the shared `decode_model_text()` (AIM Code 39 Extended via `AIM_MAP`, plus the
  `$`-delimited label-printer format) — the same routine the main screen uses. The field
  selects its text on focus so a scan overwrites it, and the scanner's trailing Enter only
  cleanses the field (it never submits the dialog).
- **Save to DB** validates (model required, ≥1 point, names non-empty and unique, `lower < upper`)
  then writes via `upsert_model_spec()` on a background thread (`SpecUpsertThread`) so the UI
  never freezes.
- `upsert_model_spec(model, points)` performs an idempotent **`DELETE` + `INSERT`** of the
  model's rows in one committed transaction, and returns the row count.
- On success the points are cached to `gui_mode5_config.json`; if the saved model is the active
  one it refreshes in place, otherwise the app offers to switch to it.

The interactive [HIOKI_UI_mockup.html](HIOKI_UI_mockup.html) demonstrates the same flow (its
**Register Model** tab, simulated) including the generated SQL.

---

## Point cards (touch navigation)

Instead of stepping with Prev/Next, the screen shows **one tappable card per point**, placed
between the measured value and the judgement:

```
┌──────────────┐ ┌──────────────┐ ┌──────────────┐
│     A-B      │ │     B-C      │ │     D-E      │   ← tap any card to jump to it
│  1 – 3 Ω     │ │  4 – 7 Ω     │ │  4 – 9 Ω     │
│    seq 1     │ │    seq 2     │ │    seq 3     │
└──────────────┘ └──────────────┘ └──────────────┘
   ^ active card highlighted (cyan border)
```

- Tapping a card jumps **straight to that point** (`on_point_card_tapped`) — no stepping
  through the ones in between. It sets the limits, header, and judging range immediately.
- Auto-advance still drives the sequence during measurement; it just re-highlights the next
  card. Cards are rebuilt **only when the point set changes** (tracked by `_cards_signature()`),
  so advancing mid-measurement causes no widget churn or flicker.
- **⟲ Reset to first point** and **⚙ Edit Spec** remain as buttons. Reset needs an active
  sequence; Edit Spec is always available.
- With no sequence (single-range model) no cards are shown and the limits stay editable.

---

## Startup schema guard

Because `insert_to_mssql()` now always writes `Point / Seq / LowerLimit / UpperLimit`, the
`resistance` columns and the `resistance_spec` table **must exist** or every upload fails.
`setup_services.sh` provisions the Pi but does **not** touch the central DB schema — migration
`001` is a one-time step against `ENGINEER_DB`.

To catch a missed migration, the app runs an optional, non-fatal guard at startup:

- `check_schema()` (in `insert_resistance2db.py`) probes `INFORMATION_SCHEMA` for the
  `resistance_spec` table and the four new `resistance` columns. It never raises.
- `SchemaCheckThread` runs it off the main thread (no UI freeze); `on_schema_checked()` logs
  the result. If the schema is out of date it prints a `WARNING` and shows an on-screen line:
  *"! DB schema out of date — … Apply migrations/001_multipoint_spec.sql to ENGINEER_DB."*
- A merely **unreachable** server is not flagged (the offline queue handles that); only a
  reachable server with a missing table/columns triggers the warning.

---

## Files changed

| File | Change |
|------|--------|
| `migrations/001_multipoint_spec.sql` | Create `resistance_spec`; add 4 columns to `resistance` (re-runnable). |
| `migrations/002_seed_500D.sql` | Seed the `500D` example sequence. |
| `insert_resistance2db.py` | `insert_to_mssql(..., point, seq, lower, upper)`; new `fetch_model_spec(model)`; new `upsert_model_spec(model, points)`; shared `_open_connection()`. |
| `db_upload_manager.py` | Thread the 4 fields through the offline queue and batch retry (old queued records still flush via `.get()`). |
| `main.py` | `SpecFetchThread`, spec load/cache, judge-against-current-point, auto-advance (wrap), point label + read-only limits + Prev/Reset/Next buttons, CSV headers; `ModelSpecDialog` + `SpecUpsertThread` + ⚙ Edit Spec button for in-app spec registration. |

## Deploy / verify

1. Run `migrations/001_*.sql` then `002_*.sql` against `ENGINEER_DB`.
2. `python insert_resistance2db.py` — inserts a tagged `500D` row and prints the fetched spec.
3. Launch app, select `500D`, feed 3 stable readings → UI advances A-B → B-C → D-E → A-B,
   3 rows written with matching `Point/Seq/LowerLimit/UpperLimit`.
4. Offline test: stop SQL mid-run → readings queue with the new fields; restart → batch flush.
5. Backward-compat: a model with no points still judges on the single editable range and
   inserts `NULL` point columns.
6. Registration: tap **⚙ Edit Spec**, enter a new model + points, **Save to DB** → confirm the
   rows land in `resistance_spec`; re-save to confirm the `DELETE`+`INSERT` upsert replaces them.
