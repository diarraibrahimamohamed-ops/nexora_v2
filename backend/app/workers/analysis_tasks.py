"""
app/workers/analysis_tasks.py — Analyse de séquences / FASTA lourdes (Celery).
Activée lorsque la longueur dépasse le seuil navigateur.
"""
import traceback

from app.database import SessionLocal
from app.models.sequence import Analysis, AnalysisStatus
from app.workers.celery_app import celery_app


def _analyze_chunked(sequence: str) -> dict:
    """Réutilise la logique chunkée de l'API (sans dépendre du request cycle)."""
    from app.api.v1.analysis import _clean, _analyze_fragment

    CHUNK_SIZE = 20_000
    seq = _clean(sequence)
    fragments = []
    idx = 1
    for i in range(0, len(seq), CHUNK_SIZE):
        frag = seq[i : i + CHUNK_SIZE]
        fragments.append(_analyze_fragment(frag, idx, i + 1))
        idx += 1
    return {
        "input_length": len(seq),
        "chunks": len(fragments),
        "fragments": fragments,
        "mode": "async_server",
    }


@celery_app.task(name="analysis.run", bind=True, max_retries=1)
def run_analysis_task(self, analysis_id: int, sequence: str) -> dict:
    db = SessionLocal()
    try:
        a = db.query(Analysis).filter(Analysis.id == analysis_id).first()
        if a:
            a.status = AnalysisStatus.running
            db.commit()

        result = _analyze_chunked(sequence)

        if a:
            data = dict(a.data or {})
            data["async_sequence_result"] = result
            a.data = data
            a.status = AnalysisStatus.completed
            db.commit()

        return {"analysis_id": analysis_id, "status": "completed", "result": result}
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"[analysis_tasks] {analysis_id} failed:\n{traceback.format_exc()}", flush=True)
        try:
            a = db.query(Analysis).filter(Analysis.id == analysis_id).first()
            if a:
                a.status = AnalysisStatus.failed
                data = dict(a.data or {})
                data["error"] = err
                a.data = data
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
    query: str,
    reference: str,
    options: dict = None
) -> dict:
    """Exécute l'alignement Smith-Waterman-Gotoh et la détection de variants en tâche Celery."""
    from app.core.sequence.afribio_engine import SmithWatermanGotoh, VariantCaller

    opts = options or {}
    sw = SmithWatermanGotoh(
        match_score=opts.get("match", 5),
        mismatch_penalty=opts.get("mismatch", -4),
        gap_open=opts.get("gapOpen", -10),
        gap_extend=opts.get("gapExtend", -1),
        seq_type=opts.get("seqType", "DNA")
    )

    alignment = sw.align(query, reference)
    variants = VariantCaller.call_variants(alignment)
    vcf = VariantCaller.generate_vcf(variants, sample_name=opts.get("sampleName", "Nexora_Sample"))

    return {
        "job_id": job_id,
        "status": "completed",
        "alignment": alignment,
        "variants": variants,
        "vcf": vcf,
        "metrics": {
            "identity": alignment.get("identity", 0.0),
            "similarity": alignment.get("similarity", 0.0),
            "coverage": alignment.get("coverage", 0.0),
            "score": alignment.get("score", 0),
            "length": alignment.get("length", 0),
            "variants_count": len(variants),
            "snps_count": sum(1 for v in variants if v.get("type") == "SNP"),
            "insertions_count": sum(1 for v in variants if v.get("type") == "insertion"),
            "deletions_count": sum(1 for v in variants if v.get("type") == "deletion")
        }
    }


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
