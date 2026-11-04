"""
app/workers/analysis_tasks.py — Analyse de séquences / FASTA lourdes (Celery).
Activée lorsque la longueur dépasse le seuil navigateur.
"""
import traceback

from app.database import SessionLocal
from app.models.sequence import Analysis, AnalysisStatus, Sequence
from app.workers.celery_app import celery_app


def _analyze_chunked(sequence: str) -> dict:
    """Use the canonical chunk/translation implementation for every execution mode."""
    from app.api.v1.analysis import _clean, _sync_analyze

    result = _sync_analyze(_clean(sequence))
    result["mode"] = "async_server"
    return result


@celery_app.task(name="analysis.run", bind=True, max_retries=1)
def run_analysis_task(self, analysis_id: int, sequence: str | None = None) -> dict:
    """Run a persisted sequence analysis; Celery payloads carry identifiers, not raw sequences."""
    import hashlib

    from app.services.scientific_provenance import (
        build_analysis_manifest,
        sha256_json,
        software_fingerprint,
    )

    db = SessionLocal()
    analysis = None
    try:
        analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if analysis is None:
            return {"analysis_id": analysis_id, "status": "failed", "error": "Analyse introuvable."}

        if analysis.status == AnalysisStatus.completed:
            existing_result = (analysis.data or {}).get("async_sequence_result")
            if isinstance(existing_result, dict):
                return {
                    "analysis_id": analysis_id,
                    "status": "completed",
                    "result": existing_result,
                    "provenance": (analysis.data or {}).get("scientific_provenance"),
                    "idempotent_replay": True,
                }

        source_record = None
        if sequence is None:
            source_record = (
                db.query(Sequence)
                .filter(Sequence.analysis_id == analysis_id)
                .order_by(Sequence.id.asc())
                .first()
            )
            sequence = source_record.sequence if source_record else None
        if not sequence:
            raise ValueError("Aucune séquence persistée pour cette analyse.")

        sequence_sha256 = hashlib.sha256(sequence.encode("ascii")).hexdigest()
        data = dict(analysis.data or {})
        declared_sha256 = data.get("input_sequence_sha256")
        if declared_sha256 and declared_sha256 != sequence_sha256:
            raise ValueError("L'empreinte de la séquence persistée ne correspond plus au manifeste d'entrée.")

        analysis.status = AnalysisStatus.running
        db.commit()

        result = _analyze_chunked(sequence)
        output_sha256 = sha256_json(result)
        input_record = {
            "role": "primary_sequence",
            "object_type": "sequences" if source_record else "legacy_task_argument",
            "object_id": str(source_record.id) if source_record else f"analysis:{analysis_id}",
            "sha256": sequence_sha256,
            "length": len(sequence),
            "alphabet": "DNA/IUPAC-ACGTN",
        }
        provenance = build_analysis_manifest(
            algorithm="N3XORA_SEQUENCE_CHUNK_ANALYSIS",
            algorithm_version="N3XORA_SEQUENCE_CHUNK_ANALYSIS_V2",
            inputs=[input_record],
            parameters={
                "chunk_size": 20_000,
                "server_sync_chunk_threshold": 50_000,
                "reading_frames": [1, 2, 3],
                "translation_frame_scope": "whole_sequence_with_boundary_carry",
                "gc_denominator": "called_bases_ACGT_excluding_N",
                "accepted_alphabet": "ACGTN",
            },
            output_sha256=output_sha256,
            execution={
                "mode": "celery",
                "task_name": "analysis.run",
                "task_id": getattr(getattr(self, "request", None), "id", None),
                "queue": "analysis",
                "outbox_event_key": data.get("outbox_event_key"),
            },
            software=software_fingerprint("app.api.v1.analysis", dependencies=()),
            interpretation_limit=(
                "Deterministic sequence composition and translation only; "
                "no variant confidence, clinical inference or experimental validation."
            ),
        )

        data["async_sequence_result"] = result
        data["scientific_provenance"] = provenance
        data["scientific_output_sha256"] = output_sha256
        analysis.data = data
        analysis.status = AnalysisStatus.completed
        db.commit()

        return {
            "analysis_id": analysis_id,
            "status": "completed",
            "result": result,
            "provenance": provenance,
        }
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"[analysis_tasks] {analysis_id} failed:\n{traceback.format_exc()}", flush=True)
        try:
            current = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            if current:
                current.status = AnalysisStatus.failed
                data = dict(current.data or {})
                data["error"] = err
                current.data = data
                db.commit()
        except Exception:
            db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {"analysis_id": analysis_id, "status": "failed", "error": err}
    finally:
        db.close()


@celery_app.task(name="analysis.run_afribio_alignment", bind=True, max_retries=1)
def run_afribio_alignment_task(
    self,
    job_id: str,
    query: str | None = None,
    reference: str | None = None,
    options: dict = None,
) -> dict:
    """Run AfriBio alignment; persisted workflows pass an analysis ID only."""
    import hashlib

    from app.core.sequence.afribio_engine import SmithWatermanGotoh, VariantCaller
    from app.services.scientific_provenance import (
        build_analysis_manifest,
        sha256_json,
        software_fingerprint,
    )

    opts = options or {}
    persisted_analysis = None
    source_records = []
    db = None
    persisted_mode = query is None and reference is None

    try:
        if persisted_mode:
            analysis_id = int(job_id)
            db = SessionLocal()
            persisted_analysis = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            if persisted_analysis is None:
                raise ValueError("Dossier d'alignement introuvable.")
            if persisted_analysis.status == AnalysisStatus.completed:
                cached = (persisted_analysis.data or {}).get("async_alignment_result")
                if isinstance(cached, dict):
                    return {
                        "job_id": str(analysis_id),
                        "analysis_id": analysis_id,
                        "status": "completed",
                        "result": cached,
                        "provenance": (persisted_analysis.data or {}).get("scientific_provenance"),
                        "idempotent_replay": True,
                    }
            source_records = (
                db.query(Sequence)
                .filter(Sequence.analysis_id == analysis_id)
                .order_by(Sequence.id.asc())
                .all()
            )
            by_header = {record.header: record for record in source_records}
            query_record = by_header.get("query")
            reference_record = by_header.get("reference")
            if query_record is None or reference_record is None:
                raise ValueError("Les séquences query et reference doivent être persistées.")
            query = query_record.sequence
            reference = reference_record.sequence
            persisted_analysis.status = AnalysisStatus.running
            db.commit()
        elif not query or not reference:
            raise ValueError("Les séquences query et reference sont obligatoires.")

        engine = SmithWatermanGotoh(
            match_score=opts.get("match", 5),
            mismatch_penalty=opts.get("mismatch", -4),
            gap_open=opts.get("gapOpen", -10),
            gap_extend=opts.get("gapExtend", -1),
            seq_type=opts.get("seqType", "DNA"),
            max_alignment_cells=opts.get("maxAlignmentCells", 5_000_000),
        )
        alignment = engine.align(query, reference)
        variants = VariantCaller.call_variants(alignment)
        vcf = VariantCaller.generate_vcf(
            variants,
            sample_name=opts.get("sampleName", "Nexora_Sample"),
        )
        metrics = {
            "identity": alignment.get("identity", 0.0),
            "similarity": alignment.get("similarity", 0.0),
            "coverage": alignment.get("coverage", 0.0),
            "score": alignment.get("score", 0),
            "length": alignment.get("length", 0),
            "variants_count": len(variants),
            "snps_count": sum(1 for v in variants if v.get("type") == "SNP"),
            "insertions_count": sum(1 for v in variants if v.get("type") == "insertion"),
            "deletions_count": sum(1 for v in variants if v.get("type") == "deletion"),
        }
        result = {
            "alignment": alignment,
            "variants": variants,
            "vcf": vcf,
            "metrics": metrics,
        }

        provenance = None
        if persisted_mode and persisted_analysis is not None:
            input_records = [
                {
                    "role": record.header,
                    "object_type": "sequences",
                    "object_id": str(record.id),
                    "sha256": hashlib.sha256(record.sequence.encode("ascii")).hexdigest(),
                    "length": len(record.sequence),
                }
                for record in source_records
                if record.header in {"query", "reference"}
            ]
            output_sha256 = sha256_json(result)
            provenance = build_analysis_manifest(
                algorithm="Smith-Waterman-Gotoh",
                algorithm_version="AfriBio-Core-N3XORA-2026",
                inputs=input_records,
                parameters={
                    "match_score": engine.match_score,
                    "mismatch_penalty": engine.mismatch_penalty,
                    "gap_open": engine.gap_open,
                    "gap_extend": engine.gap_extend,
                    "sequence_type": engine.seq_type,
                    "max_alignment_cells": engine.max_alignment_cells,
                    "traceback": "state_aware_H_E_F",
                    "variant_calling": "alignment_derived_unverified",
                },
                output_sha256=output_sha256,
                execution={
                    "mode": "celery_scientific_workflow",
                    "task_name": "analysis.run_afribio_alignment",
                    "task_id": getattr(getattr(self, "request", None), "id", None),
                    "queue": "analysis",
                    "analysis_id": persisted_analysis.id,
                    "scientific_workflow_run_id": (persisted_analysis.data or {}).get("scientific_workflow_run_id"),
                },
                software=software_fingerprint("app.core.sequence.afribio_engine", dependencies=()),
                interpretation_limit=(
                    "Local pairwise alignment and variants inferred from that alignment only. "
                    "No read-level evidence, genomic coordinate normalization or experimental variant quality."
                ),
            )
            data = dict(persisted_analysis.data or {})
            data["async_alignment_result"] = result
            data["scientific_provenance"] = provenance
            data["scientific_output_sha256"] = output_sha256
            persisted_analysis.data = data
            persisted_analysis.status = AnalysisStatus.completed
            db.commit()

        return {
            "job_id": str(job_id),
            "analysis_id": int(job_id) if persisted_mode else None,
            "status": "completed",
            **result,
            "provenance": provenance,
        }
    except Exception as exc:
        if persisted_mode and db is not None and persisted_analysis is not None:
            try:
                current = db.query(Analysis).filter(Analysis.id == persisted_analysis.id).first()
                if current:
                    current.status = AnalysisStatus.failed
                    data = dict(current.data or {})
                    data["error"] = f"{type(exc).__name__}: {exc}"
                    current.data = data
                    db.commit()
            except Exception:
                db.rollback()
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        raise
    finally:
        if db is not None:
            db.close()


@celery_app.task(name="analysis.run_afribio_phylo", bind=True, max_retries=1)
def run_afribio_phylo_task(self, job_id: str, sequences: list) -> dict:
    """Construit un arbre phylogénétique NJ en tâche Celery."""
    from app.core.sequence.afribio_engine import PhylogeneticEngine

    tree_data = PhylogeneticEngine.build_nj_tree(sequences)
    return {
        "job_id": job_id,
        "status": "completed",
        "tree": tree_data
    }
