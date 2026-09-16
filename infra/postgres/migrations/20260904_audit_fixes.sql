-- Migration audit 2026-09-04 : unifier job_status done → completed
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_enum e
    JOIN pg_type t ON e.enumtypid = t.oid
    WHERE t.typname = 'job_status' AND e.enumlabel = 'done'
  ) THEN
    ALTER TYPE job_status RENAME VALUE 'done' TO 'completed';
  END IF;
END $$;

COMMENT ON TABLE qsa_jobs IS 'HSA (Heuristic Stability Analysis) — ex QSA ; aucun calcul quantique';
