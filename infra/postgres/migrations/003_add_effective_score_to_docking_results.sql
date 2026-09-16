-- Preserve legacy effective_dg while storing the empirical aggregated score explicitly.
ALTER TABLE docking_results
    ADD COLUMN IF NOT EXISTS effective_score NUMERIC(10, 4);