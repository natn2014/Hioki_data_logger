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