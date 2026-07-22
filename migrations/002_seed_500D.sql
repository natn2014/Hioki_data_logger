-- Seed 002: example point sequence for model 500D.
-- Re-runnable: clears then re-inserts this model's rows.
DELETE FROM dbo.resistance_spec WHERE Model = '500D';

INSERT INTO dbo.resistance_spec (Model, Seq, PointName, LowerLimit, UpperLimit) VALUES
    ('500D', 1, 'A-B', 1, 3),
    ('500D', 2, 'B-C', 4, 7),
    ('500D', 3, 'D-E', 4, 9);
GO
