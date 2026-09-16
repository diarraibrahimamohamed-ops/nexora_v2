"""Async structure-resolution task for the Phase 3 research workflow."""
from __future__ import annotations

import os
from pathlib import Path

from app.config import settings
from app.database import SessionLocal
from app.models.research import ResearchStructureJob, ResearchTarget
from app.workers.celery_app import celery_app


def _update(db, job_id: int, **values):
    job = db.query(ResearchStructureJob).filter(ResearchStructureJob.id == job_id).first()
    if not job:
        return
    for k, v in values.items():
        setattr(job, k, v)
    db.commit()


@celery_app.task(bind=True, name="research.resolve_structure", max_retries=1, default_retry_delay=30, acks_late=True)
def resolve_research_structure_task(self, job_id: int) -> dict:
    db = SessionLocal()
    try:
        job = db.query(ResearchStructureJob).filter(ResearchStructureJob.id == job_id).first()
        if not job:
            return {"success": False, "error": "structure job introuvable"}
        target = db.query(ResearchTarget).filter(ResearchTarget.id == job.target_id).first()
        if not target:
            _update(db, job_id, status="failed", error="cible introuvable", progress=0)
            return {"success": False, "error": "cible introuvable"}

        _update(db, job_id, status="running", celery_task_id=self.request.id, progress=5, error=None)
        out_dir = Path(settings.TEMP_DIR) / "research_structures" / f"study_{job.study_id}" / f"target_{target.id}"
        out_dir.mkdir(parents=True, exist_ok=True)

        provider = job.provider
        metadata = {}
        path = None
        error = None
        if provider == "auto_rcsb":
            from app.core.protein.structure_resolver import resolve_experimental_pdb
            _update(db, job_id, progress=20)
            path, error, metadata = resolve_experimental_pdb(
                target.protein_sequence,
                str(out_dir),
                identity_cutoff=settings.STRUCTURE_IDENTITY_CUTOFF,
                evalue_cutoff=settings.STRUCTURE_EVALUE_CUTOFF,
            )
            source_type = "experimental_pdb"
        elif provider == "esmfold":
            from app.core.protein.esmfold_runner import build_esmfold_structure
            _update(db, job_id, progress=15)
            path, error, metadata = build_esmfold_structure(target.protein_sequence, str(out_dir), nvidia_api_key=settings.NVIDIA_API_KEY or None)
            source_type = "predicted_esmfold"
        elif provider == "colabfold":
            from app.core.protein.colabfold_runner import build_colabfold_structure
            _update(db, job_id, progress=15)
            path, error, metadata = build_colabfold_structure(
                target.protein_sequence,
                str(out_dir),
                binary=settings.COLABFOLD_BINARY,
                num_models=settings.COLABFOLD_NUM_MODELS,
                num_recycle=settings.COLABFOLD_NUM_RECYCLE,
            )
            source_type = "predicted_colabfold"
        else:
            source_type = provider
            error = f"provider de structure non supporté: {provider}"

        if error or not path:
            _update(db, job_id, status="failed", error=error or "structure non produite", progress=0, metadata_json=metadata or {})
            return {"success": False, "job_id": job_id, "error": error or "structure non produite"}

        from app.core.protein.structure_validation import validate_structure
        _update(db, job_id, progress=80)
        report = validate_structure(
            path,
            target.protein_sequence,
            source_type,
            identity_cutoff=settings.STRUCTURE_IDENTITY_CUTOFF,
            coverage_cutoff=settings.STRUCTURE_COVERAGE_CUTOFF,
        )
        if source_type == "experimental_pdb" and metadata.get("entry_id"):
            try:
                import httpx
                entry_id = str(metadata["entry_id"]).upper()
                with httpx.Client(timeout=20.0, follow_redirects=True) as client:
                    entry = client.get(f"https://data.rcsb.org/rest/v1/core/entry/{entry_id}")
                    if entry.is_success:
                        payload = entry.json()
                        info = payload.get("rcsb_entry_info", {}) or {}
                        exptl = payload.get("exptl", []) or []
                        metadata["experimental_quality"] = {
                            "experimental_method": info.get("experimental_method") or (exptl[0].get("method") if exptl else None),
                            "resolution_combined": info.get("resolution_combined"),
                            "deposited_polymer_entity_count": info.get("deposited_polymer_entity_instance_count"),
                        }
            except Exception as exc:
                metadata["experimental_quality_lookup"] = {"status": "unavailable", "error_type": type(exc).__name__}

        report["experimental_quality"] = metadata.get("experimental_quality") if source_type == "experimental_pdb" else None
        target.structure_source = source_type
        target.structure_path = str(Path(path).resolve())
        target.structure_accession = metadata.get("entry_id") or target.structure_accession
        target.structure_status = report["status"]
        best = report["sequence_match"]["best_chain"]
        target.structure_sequence_identity = best["identity"]
        target.structure_sequence_coverage = best["coverage"]
        target.structure_confidence = report["predicted_confidence"]["normalized_mean"]
        target.structure_validation = report
        target.provenance = {
            **(target.provenance or {}),
            "structure_resolution": metadata,
            "resolved_provider": provider,
            "validation": report,
        }
        db.commit()
        _update(db, job_id, status="completed", progress=100, metadata_json={"provider": provider, "resolution": metadata, "validation_status": report["status"]})
        return {"success": True, "job_id": job_id, "target_id": target.id, "structure_path": str(Path(path).resolve()), "validation": report}
    except Exception as exc:
        db.rollback()
        _update(db, job_id, status="failed", error=f"{type(exc).__name__}: {exc}", progress=0)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {"success": False, "job_id": job_id, "error": str(exc)}
    finally:
        db.close()
