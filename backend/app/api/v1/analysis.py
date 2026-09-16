"""
api/v1/analysis.py
POST /api/v1/analysis/sequence     → remplace s.php (analyse chunked ADN)
POST /api/v1/analysis/save         → remplace api.php?action=save_analysis
GET  /api/v1/analysis/history      → remplace get_analyses.php
GET  /api/v1/analysis/stats        → remplace get_stats.php
DELETE /api/v1/analysis/{id}       → remplace delete_analysis.php
GET  /api/v1/analysis/{id}         → remplace api.php?action=get_docking_results
"""
import re
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from pydantic import BaseModel

from app.database import get_db
from app.models.sequence import Analysis, Sequence, Mutation, AntibioticProfile, AnalysisStatus
from fastapi.security import OAuth2PasswordBearer
from app.models.user import User
from app.services.auth_service import get_current_user
from app.workers.analysis_tasks import run_analysis_task

router = APIRouter(prefix="/analysis", tags=["analysis"])

_opt_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token", auto_error=False)


def _get_opt_user(token: str = Depends(_opt_scheme), db: Session = Depends(get_db)):
    """Auth optionnelle (analyse sync / async anonyme)."""
    if not token:
        return None
    try:
        from app.services.auth_service import decode_token
        payload = decode_token(token)
        uid = payload.get("sub")
        return db.query(User).filter(User.id == int(uid)).first() if uid else None
    except Exception:
        return None

# Seuil aligné sur le frontend async-optimizer (navigateur)
BROWSER_SEQUENCE_THRESHOLD = 100_000
SERVER_SYNC_CHUNK_THRESHOLD = 50_000
CHUNK_SIZE = 20_000


# ── Helpers séquence (migration exacte de s.php) ────────────────────────────

def _clean(seq: str) -> str:
    return re.sub(r'[^ACGT]', '', seq.upper())

def _gc(seq: str) -> float:
    if not seq: return 0.0
    return round((seq.count('G') + seq.count('C')) / len(seq) * 100, 3)

def _nuc_counts(seq: str) -> dict:
    return {'A': seq.count('A'), 'C': seq.count('C'), 'G': seq.count('G'),
            'T': seq.count('T'), 'length': len(seq)}

_CODON_TABLE = {
    'UUU':'F','UUC':'F','UUA':'L','UUG':'L','UCU':'S','UCC':'S','UCA':'S','UCG':'S',
    'UAU':'Y','UAC':'Y','UAA':'*','UAG':'*','UGU':'C','UGC':'C','UGA':'*','UGG':'W',
    'CUU':'L','CUC':'L','CUA':'L','CUG':'L','CCU':'P','CCC':'P','CCA':'P','CCG':'P',
    'CAU':'H','CAC':'H','CAA':'Q','CAG':'Q','CGU':'R','CGC':'R','CGA':'R','CGG':'R',
    'AUU':'I','AUC':'I','AUA':'I','AUG':'M','ACU':'T','ACC':'T','ACA':'T','ACG':'T',
    'AAU':'N','AAC':'N','AAA':'K','AAG':'K','AGU':'S','AGC':'S','AGA':'R','AGG':'R',
    'GUU':'V','GUC':'V','GUA':'V','GUG':'V','GCU':'A','GCC':'A','GCA':'A','GCG':'A',
    'GAU':'D','GAC':'D','GAA':'E','GAG':'E','GGU':'G','GGC':'G','GGA':'G','GGG':'G',
}

def _translate(rna: str, frame: int = 0) -> str:
    prot = ""
    for i in range(frame, len(rna) - 2, 3):
        aa = _CODON_TABLE.get(rna[i:i+3], 'X')
        prot += aa
    return prot

def _analyze_fragment(frag: str, index: int, start: int) -> dict:
    rna = frag.replace('T', 'U')
    return {
        'index': index, 'start_pos': start, 'end_pos': start + len(frag) - 1,
        'length': len(frag), 'gc_percent': _gc(frag), 'nuc_counts': _nuc_counts(frag),
        'adn_template': frag, 'arn': rna,
        'translations': {
            'frame_1': _translate(rna, 0),
            'frame_2': _translate(rna, 1),
            'frame_3': _translate(rna, 2),
        }
    }


def _sync_analyze(seq: str) -> dict:
    fragments = []
    if len(seq) <= SERVER_SYNC_CHUNK_THRESHOLD:
        fragments.append(_analyze_fragment(seq, 1, 1))
    else:
        idx = 1
        for i in range(0, len(seq), CHUNK_SIZE):
            frag = seq[i:i + CHUNK_SIZE]
            fragments.append(_analyze_fragment(frag, idx, i + 1))
            idx += 1
    return {
        "input_length": len(seq),
        "chunks": len(fragments),
        "fragments": fragments,
        "mode": "sync",
    }


# ── Schemas ──────────────────────────────────────────────────────────────────

class SequenceRequest(BaseModel):
    sequence: str
    force_async: bool = False

class SaveAnalysisRequest(BaseModel):
    sequence_name:     Optional[str] = None
    original_sequence: Optional[str] = None
    reference_sequence: Optional[str] = None
    sequence_type:     Optional[str] = "DNA"
    mutations:         Optional[list] = []
    resistance_data:   Optional[dict] = {}
    protein_data:      Optional[dict] = None
    # user_id ignoré volontairement (anti-IDOR) — conservé pour compat body frontend
    user_id:           Optional[int] = None


# ── Routes ───────────────────────────────────────────────────────────────────

@router.post("/sequence")
def analyze_sequence(
    body: SequenceRequest,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(_get_opt_user),
):
    """
    Analyse ADN chunkée.
    - ≤ BROWSER_SEQUENCE_THRESHOLD : synchrone
    - > seuil (ou force_async) : Celery file `analysis`, poll via /sequence/{id}/status
    """
    seq = _clean(body.sequence)
    if not seq:
        raise HTTPException(400, "Séquence vide ou invalide (seuls ACGT acceptés)")

    if len(seq) <= BROWSER_SEQUENCE_THRESHOLD and not body.force_async:
        return _sync_analyze(seq)

    # Traitement asynchrone serveur (séquences trop lourdes pour le navigateur)
    analysis = Analysis(
        user_id=current_user.id if current_user else None,
        name=f"async_seq_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        type="DNA",
        status=AnalysisStatus.pending,
        data={"input_length": len(seq), "mode": "async_server"},
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)

    task = run_analysis_task.apply_async(
        args=[analysis.id, seq],
        queue="analysis",
    )
    data = dict(analysis.data or {})
    data["celery_task_id"] = task.id
    analysis.data = data
    db.commit()

    return {
        "async": True,
        "analysis_id": analysis.id,
        "task_id": task.id,
        "status": "pending",
        "input_length": len(seq),
        "message": (
            f"Séquence > {BROWSER_SEQUENCE_THRESHOLD} nt — "
            "traitement serveur asynchrone. Suivre GET /analysis/sequence/{analysis_id}/status"
        ),
    }


@router.get("/sequence/{analysis_id}/status")
def get_async_sequence_status(
    analysis_id: int,
    db: Session = Depends(get_db),
    current_user: Optional[User] = Depends(_get_opt_user),
):
    a = db.query(Analysis).filter(Analysis.id == analysis_id).first()
    if not a:
        raise HTTPException(404, "Analyse introuvable")
    # Si l'analyse est liée à un user, exiger le propriétaire
    if a.user_id is not None:
        if not current_user or current_user.id != a.user_id:
            raise HTTPException(403, "Accès refusé")
    status_val = a.status.value if hasattr(a.status, "value") else str(a.status)
    payload = {
        "analysis_id": a.id,
        "status": status_val,
        "input_length": (a.data or {}).get("input_length"),
    }
    if status_val == AnalysisStatus.completed.value:
        payload["result"] = (a.data or {}).get("async_sequence_result")
    if status_val == AnalysisStatus.failed.value:
        payload["error"] = (a.data or {}).get("error")
    return payload


@router.post("/save")
def save_analysis(
    body: SaveAnalysisRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Persiste une analyse — authentification obligatoire ; body.user_id ignoré (anti-IDOR)."""
    analysis = Analysis(
        user_id=current_user.id,
        name=body.sequence_name or f"analysis_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}",
        type=body.sequence_type or "DNA",
        status=AnalysisStatus.completed,
        data={
            "protein_data": body.protein_data,
            "resistance_data": body.resistance_data,
            "gc_content": _gc(body.original_sequence or ""),
        },
    )
    db.add(analysis)
    db.flush()

    if body.original_sequence:
        db.add(Sequence(
            analysis_id=analysis.id,
            header=body.sequence_name or "sequence",
            sequence=body.original_sequence,
            length=len(body.original_sequence),
        ))

    for m in (body.mutations or []):
        db.add(Mutation(
            analysis_id=analysis.id,
            type=m.get("type", "Unknown"),
            ref_pos=m.get("position"),
            qry_pos=m.get("position"),
            ref_base=m.get("original"),
            qry_base=m.get("mutated"),
        ))

    for ab, data in (body.resistance_data or {}).items():
        db.add(AntibioticProfile(
            analysis_id=analysis.id,
            antibiotic=ab,
            status=str(data.get("resistance_level", data.get("evidence_level", "unknown"))),
            score=data.get("confidence_score"),
        ))

    db.commit()
    return {"success": True, "analysis_id": analysis.id}


@router.get("/history")
def get_history(
    skip:         int          = Query(0, ge=0),
    limit:        int          = Query(20, le=100),
    db:           Session      = Depends(get_db),
    current_user: User         = Depends(get_current_user),
):
    """Remplace get_analyses.php — liste les analyses de l'utilisateur."""
    analyses = (
        db.query(Analysis)
        .filter(Analysis.user_id == current_user.id)
        .order_by(Analysis.created_at.desc())
        .offset(skip).limit(limit).all()
    )
    return {
        "success": True,
        "analyses": [
            {
                "id":         a.id,
                "name":       a.name,
                "type":       a.type,
                "status":     a.status,
                "created_at": a.created_at.isoformat(),
                "data":       a.data,
            }
            for a in analyses
        ],
    }


@router.get("/stats")
def get_stats(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    """Remplace get_stats.php."""
    total     = db.query(func.count(Analysis.id)).filter(Analysis.user_id == current_user.id).scalar()
    completed = db.query(func.count(Analysis.id)).filter(
        Analysis.user_id == current_user.id,
        Analysis.status  == AnalysisStatus.completed,
    ).scalar()
    mutations = db.query(func.count(Mutation.id)).join(Analysis).filter(
        Analysis.user_id == current_user.id).scalar()
    return {"success": True, "total_analyses": total, "completed": completed, "total_mutations": mutations}


@router.delete("/{analysis_id}")
def delete_analysis(
    analysis_id:  int,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """Remplace delete_analysis.php."""
    a = db.query(Analysis).filter(Analysis.id == analysis_id, Analysis.user_id == current_user.id).first()
    if not a:
        raise HTTPException(404, "Analyse introuvable")
    db.delete(a)
    db.commit()
    return {"success": True, "deleted_id": analysis_id}


@router.get("/{analysis_id}")
def get_analysis(
    analysis_id:  int,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    a = db.query(Analysis).filter(Analysis.id == analysis_id, Analysis.user_id == current_user.id).first()
    if not a:
        raise HTTPException(404, "Analyse introuvable")
    return {
        "success": True,
        "id":      a.id,
        "name":    a.name,
        "type":    a.type,
        "status":  a.status,
        "data":    a.data,
        "sequences": [{"id": s.id, "header": s.header, "length": s.length} for s in a.sequences],
        "mutations":  [{"type": m.type, "position": m.ref_pos,
                        "original": m.ref_base, "mutated": m.qry_base} for m in a.mutations],
    }


@router.post("/interpret")
def interpret_analysis(
    analysis_id: Optional[int] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Interprète une analyse sauvegardée en utilisant les fonctions d'analyse génomique avancée.
    Si analysis_id n'est pas fourni, utilise la dernière analyse de l'utilisateur.
    """
    # Récupérer l'analyse
    if analysis_id:
        analysis = db.query(Analysis).filter(
            Analysis.id == analysis_id,
            Analysis.user_id == current_user.id
        ).first()
    else:
        analysis = db.query(Analysis).filter(
            Analysis.user_id == current_user.id
        ).order_by(Analysis.created_at.desc()).first()
    
    if not analysis:
        raise HTTPException(404, "Aucune analyse trouvée")
    
    # Extraire les séquences
    seq = None
    ref = None
    
    # Chercher dans data
    if analysis.data:
        seq = analysis.data.get('original_sequence')
        ref = analysis.data.get('reference_sequence')
    
    # Chercher dans la table sequences si pas trouvé
    if not seq and analysis.sequences:
        seq = analysis.sequences[0].sequence
    
    if not seq:
        raise HTTPException(400, "Séquence originale manquante ou vide")
    
    # Importer les fonctions d'analyse génomique
    from app.services.genomic_analysis import interpret_sequence_pro
    
    # Générer l'interprétation
    result = interpret_sequence_pro(seq, ref)
    
    # Mettre à jour l'analyse avec les résultats d'interprétation
    if result.get('success'):
        analysis.data['interpretation'] = result
        analysis.data['interpreted_at'] = __import__('datetime').datetime.utcnow().isoformat()
        db.commit()
    
    return result


class AMRRequest(BaseModel):
    sequence: str
    organism_type: str = "bacterium"



@router.post("/amr")
def analyze_amr_endpoint(body: AMRRequest, current_user: User = Depends(get_current_user)):
    """Evidence-based AMR analysis; never returns a synthetic resistance %."""
    seq = _clean(body.sequence)
    if not seq:
        raise HTTPException(400, "Séquence vide ou invalide")
    if len(seq) > BROWSER_SEQUENCE_THRESHOLD:
        raise HTTPException(413, f"Séquence trop longue pour l'analyse AMR interactive (>{BROWSER_SEQUENCE_THRESHOLD} nt)")
    from app.services.amr_analysis import analyze_amr
    return analyze_amr(seq, body.organism_type)



class ProteinPropertiesRequest(BaseModel):
    sequence: str


@router.post("/protein-properties")
def protein_properties(body: ProteinPropertiesRequest, current_user: User = Depends(get_current_user)):
    """
    Calcule des propriétés physicochimiques déterministes d'une protéine.

    Les valeurs proviennent de BioPython ProteinAnalysis. La "structure secondaire"
    fournie est uniquement une classification heuristique H/E/C transparente; elle
    ne remplace pas une prédiction structurale 3D.
    """
    from Bio.SeqUtils.ProtParam import ProteinAnalysis

    seq = re.sub(r"\s+", "", body.sequence.upper())
    if not seq:
        raise HTTPException(400, "Séquence protéique vide")
    if len(seq) > 10_000:
        raise HTTPException(413, "Séquence protéique trop longue (> 10000 aa)")
    if not re.fullmatch(r"[ACDEFGHIKLMNPQRSTVWY]+", seq):
        raise HTTPException(400, "Séquence protéique invalide: acides aminés standards uniquement")

    analysis = ProteinAnalysis(seq)

    # Indice d'aliphaticité d'Ikai, calculé à partir de la composition.
    counts = {aa: seq.count(aa) for aa in "ACDEFGHIKLMNPQRSTVWY"}
    n = len(seq)
    aliphatic_index = 100.0 * (
        counts['A'] / n +
        2.9 * counts['V'] / n +
        3.9 * (counts['I'] + counts['L']) / n
    )

    # Classification structurale heuristique explicite; aucune prétention de
    # prédiction secondaire.
    helix = set('AELMK')
    sheet = set('VIYF')
    h = sum(aa in helix for aa in seq)
    e = sum(aa in sheet for aa in seq)
    c = n - h - e

    epsilon_reduced, epsilon_oxidized = analysis.molar_extinction_coefficient()
    aromaticity = analysis.aromaticity()
    instability = analysis.instability_index()
    gravy = analysis.gravy()
    mw = analysis.molecular_weight()
    pi = analysis.isoelectric_point()

    return {
        "success": True,
        "method": "BioPython ProteinAnalysis",
        "sequence_length": n,
        "molecular_weight_da": round(mw, 3),
        "isoelectric_point": round(pi, 3),
        "gravy": round(gravy, 4),
        "aromaticity": round(aromaticity, 4),
        "instability_index": round(instability, 3),
        "aliphatic_index": round(aliphatic_index, 3),
        "extinction_coefficient_reduced": int(epsilon_reduced),
        "extinction_coefficient_oxidized": int(epsilon_oxidized),
        "secondary_structure_heuristic": {
            "helix_like_pct": round(100.0 * h / n, 2),
            "sheet_like_pct": round(100.0 * e / n, 2),
            "coil_like_pct": round(100.0 * c / n, 2),
            "disclaimer": "Classification heuristique H/E/C; ne constitue pas une prédiction structurale secondaire."
        }
    }

# ── Endpoints AfriBio-Core (Alignement & Phylogénie) ──────────────────────────

class AfriBioAlignRequest(BaseModel):
    query: str
    reference: str
    match: int = 5
    mismatch: int = -4
    gap_open: int = -10
    gap_extend: int = -1
    seq_type: str = "DNA"
    sample_name: str = "Nexora_Sample"
    force_async: bool = False

class AfriBioPhyloRequest(BaseModel):
    sequences: list  # list of {"name": str, "sequence": str}
    force_async: bool = False

@router.post("/afribio/align")
def afribio_align(body: AfriBioAlignRequest):
    """
    Alignement local Smith-Waterman Gotoh + Détection de Variants + VCF 4.2.
    Séquences < 1 000 bp -> Synchrone
    Séquences >= 1 000 bp -> Celery Async
    """
    from app.core.sequence.afribio_engine import SmithWatermanGotoh, VariantCaller
    from app.workers.analysis_tasks import run_afribio_alignment_task
    import uuid

    q = body.query.strip()
    r = body.reference.strip()
    if not q or not r:
        raise HTTPException(400, "Séquences query et référence requises")

    if len(q) < 1000 and len(r) < 1000 and not body.force_async:
        sw = SmithWatermanGotoh(
            match_score=body.match,
            mismatch_penalty=body.mismatch,
            gap_open=body.gap_open,
            gap_extend=body.gap_extend,
            seq_type=body.seq_type
        )
        alignment = sw.align(q, r)
        variants = VariantCaller.call_variants(alignment)
        vcf = VariantCaller.generate_vcf(variants, sample_name=body.sample_name)

        return {
            "success": True,
            "mode": "sync",
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

    job_id = f"afribio_{uuid.uuid4().hex[:8]}"
    options = {
        "match": body.match,
        "mismatch": body.mismatch,
        "gapOpen": body.gap_open,
        "gapExtend": body.gap_extend,
        "seqType": body.seq_type,
        "sampleName": body.sample_name
    }
    task = run_afribio_alignment_task.delay(job_id, q, r, options)

    return {
        "success": True,
        "mode": "async",
        "job_id": job_id,
        "celery_task_id": task.id,
        "status": "pending"
    }

@router.post("/afribio/phylo")
def afribio_phylo(body: AfriBioPhyloRequest):
    """
    Arbre Phylogénétique Neighbor-Joining à partir de séquences multiples.
    """
    from app.core.sequence.afribio_engine import PhylogeneticEngine
    from app.workers.analysis_tasks import run_afribio_phylo_task
    import uuid

    seqs = body.sequences
    if not seqs or len(seqs) < 2:
        raise HTTPException(400, "Au moins 2 séquences requises pour la phylogénie")

    if len(seqs) <= 10 and not body.force_async:
        tree_data = PhylogeneticEngine.build_nj_tree(seqs)
        return {
            "success": True,
            "mode": "sync",
            "tree": tree_data
        }

    job_id = f"phylo_{uuid.uuid4().hex[:8]}"
    task = run_afribio_phylo_task.delay(job_id, seqs)
    return {
        "success": True,
        "mode": "async",
        "job_id": job_id,
        "celery_task_id": task.id,
        "status": "pending"
    }
