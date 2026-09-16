-- Migration: Ajouter status_message à docking_jobs
-- Date: 2026-07-31
-- Description: Ajouter une colonne status_message pour stocker les messages de progression du docking

ALTER TABLE docking_jobs ADD COLUMN IF NOT EXISTS status_message TEXT;
