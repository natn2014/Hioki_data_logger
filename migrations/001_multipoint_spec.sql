-- Migration 001: multi-point spec sequence with auto-switching
-- Target: ENGINEER_DB on 172.18.72.16
-- Safe to re-run: each step is guarded with an existence check.

-- 1. Master table: one row per measurement point of a model's sequence.
IF OBJECT_ID('dbo.resistance_spec', 'U') IS NULL
BEGIN
    CREATE TABLE dbo.resistance_spec (
        Model       VARCHAR(100) NOT NULL,
        Seq         INT          NOT NULL,   -- 1-based order in the sequence
        PointName   VARCHAR(50)  NOT NULL,   -- e.g. 'A-B'
        LowerLimit  FLOAT        NOT NULL,
        UpperLimit  FLOAT        NOT NULL,
        Active      BIT          NOT NULL CONSTRAINT DF_resistance_spec_Active DEFAULT 1,
        UpdatedAt   DATETIME     NOT NULL CONSTRAINT DF_resistance_spec_UpdatedAt DEFAULT getdate(),
        CONSTRAINT PK_resistance_spec PRIMARY KEY (Model, Seq)
    );
END
GO

-- 2. Extend the measurement table with a per-reading traceability snapshot.
--    Nullable so existing single-range inserts keep working.
IF COL_LENGTH('dbo.resistance', 'Point') IS NULL
    ALTER TABLE dbo.resistance ADD Point VARCHAR(50) NULL;
GO
IF COL_LENGTH('dbo.resistance', 'Seq') IS NULL
    ALTER TABLE dbo.resistance ADD Seq INT NULL;
GO
IF COL_LENGTH('dbo.resistance', 'LowerLimit') IS NULL
    ALTER TABLE dbo.resistance ADD LowerLimit FLOAT NULL;
GO
IF COL_LENGTH('dbo.resistance', 'UpperLimit') IS NULL
    ALTER TABLE dbo.resistance ADD UpperLimit FLOAT NULL;
GO
