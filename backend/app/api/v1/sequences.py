import re
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.models.sequence import Analysis, Sequence, AnalysisStatus
from app.models.ncbi import NcbiSequence
from app.models.user import User
from app.services.auth_service import get_current_user
from app.core.sequence.translation import translate_dna_to_protein

router = APIRouter(prefix="/sequences", tags=["sequences"])

STORAGE_DIR = Path("/storage")
STORAGE_DIR.mkdir(exist_ok=True)
MAX_FASTA_BYTES = 10 * 1024 * 1024
MAX_FASTA_TEXT = MAX_FASTA_BYTES
MAX_SEQUENCE_LENGTH = 100_000


def _parse_fasta(text: str) -> list:
    seqs, header, seq = [], "", ""
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(">"):
            if header and seq:
                seqs.append({"header": header, "sequence": seq, "length": len(seq)})
            header = line[1:]
            seq = ""
        else:
            seq += re.sub(r"[^A-Za-z]", "", line)
            if len(seq) > MAX_SEQUENCE_LENGTH:
                raise HTTPException(413, f"Séquence trop longue (maximum {MAX_SEQUENCE_LENGTH} bases/acides aminés)")
    if header and seq:
        seqs.append({"header": header, "sequence": seq, "length": len(seq)})
    return seqs


def _detect_type(seq: str) -> str:
    bases = set(seq.upper())
    if bases <= set("ATCGN"):
        return "dna"
    if bases <= set("AUCGN"):
        return "rna"
    return "protein"


@router.post("/upload", status_code=201)
async def upload_fasta(
    name: str = Query(..., min_length=1, max_length=200),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if file.content_type not in {None, "text/plain", "application/octet-stream", "chemical/x-fasta"}:
        raise HTTPException(415, "Type de fichier non supporté")
    content = await file.read(MAX_FASTA_BYTES + 1)
    if len(content) > MAX_FASTA_BYTES:
        raise HTTPException(413, "Fichier FASTA trop volumineux")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(400, "Fichier non UTF-8")
    parsed = _parse_fasta(text)
    if not parsed:
        raise HTTPException(400, "Aucune séquence FASTA valide")
    analysis = Analysis(
        user_id=current_user.id,
        name=name,
        type="DNA",
        status=AnalysisStatus.completed,
        data={"num_sequences": len(parsed), "source": file.filename},
    )
    db.add(analysis)
    db.flush()
    for s in parsed:
        db.add(Sequence(
            analysis_id=analysis.id,
            header=s["header"][:500],
            sequence=s["sequence"],
            length=s["length"],
            type=_detect_type(s["sequence"]),
        ))
    db.commit()
    return {"analysis_id": analysis.id, "sequences_parsed": len(parsed)}


class TranslateRequest(BaseModel):
    dna_sequence: str = Field(..., min_length=1, max_length=MAX_SEQUENCE_LENGTH)


@router.post("/translate")
def translate(body: TranslateRequest, _: User = Depends(get_current_user)):
    protein, err = translate_dna_to_protein(body.dna_sequence)
    if err:
        raise HTTPException(422, err)
    return {"protein_sequence": protein, "length": len(protein)}


@router.get("/ncbi/fetch")
def fetch_ncbi_sequence(
    accession: str = Query(..., min_length=1, max_length=100),
    database: str = Query("nucleotide", pattern=r"^[A-Za-z0-9_]+$"),
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    cached = db.query(NcbiSequence).filter(NcbiSequence.accession == accession).first()
    if cached:
        return {"success": True, "data": {"accession": cached.accession, "sequence_data": cached.sequence, "organism": cached.organism, "gene_name": cached.title, "length": cached.length, "from_cache": True}}
    try:
        from Bio import Entrez
        from app.config import settings
        Entrez.email = settings.NCBI_EMAIL or "nexora@example.com"
        if settings.NCBI_API_KEY:
            Entrez.api_key = settings.NCBI_API_KEY
        fetch = Entrez.efetch(db=database, id=accession, rettype="fasta", retmode="text")
        text = fetch.read()
        if not text.strip().startswith(">"):
            raise HTTPException(404, "Séquence NCBI introuvable")
        lines = text.strip().splitlines()
        header = lines[0][1:]
        seq = "".join(l for l in lines[1:] if l and not l.startswith(">"))
        entry = NcbiSequence(accession=accession, database_name=database, title=header, sequence=seq, length=len(seq))
        db.add(entry)
        db.commit()
        return {"success": True, "data": {"accession": accession, "sequence_data": seq, "header": header, "length": len(seq), "from_cache": False}}
    except ImportError:
        raise HTTPException(500, "Service NCBI indisponible")
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "Service NCBI temporairement indisponible")


@router.get("/ncbi")
def search_ncbi(
    query: str = Query(..., min_length=3, max_length=300),
    database: str = Query("nucleotide", pattern=r"^[A-Za-z0-9_]+$"),
    max_results: int = Query(10, ge=1, le=50),
    _: User = Depends(get_current_user),
):
    try:
        from Bio import Entrez
        from app.config import settings
        Entrez.email = settings.NCBI_EMAIL or "nexora@example.com"
        if settings.NCBI_API_KEY:
            Entrez.api_key = settings.NCBI_API_KEY
        handle = Entrez.esearch(db=database, term=query, retmax=max_results)
        record = Entrez.read(handle)
        ids = record.get("IdList", [])
        if not ids:
            return {"success": True, "results": [], "total_count": 0}
        summary = Entrez.esummary(db=database, id=",".join(ids))
        sdata = Entrez.read(summary)
        results = [{"accession": item.get("Caption", ""), "title": item.get("Title", ""), "organism": item.get("Organism", ""), "length": int(item.get("Length", 0))} for item in sdata]
        return {"success": True, "results": results, "total_count": int(record.get("Count", 0))}
    except ImportError:
        raise HTTPException(500, "Service NCBI indisponible")
    except Exception:
        raise HTTPException(502, "Service NCBI temporairement indisponible")


class SaveFastaRequest(BaseModel):
    accession: str = Field(..., min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    sequence: str = Field(..., min_length=1, max_length=MAX_SEQUENCE_LENGTH)
    header: Optional[str] = Field(None, max_length=500)


@router.post("/save-fasta")
def save_fasta_sequence(body: SaveFastaRequest, current_user: User = Depends(get_current_user)):
    """Sauvegarde une séquence dans un fichier dont le nom est généré côté serveur."""
    try:
        user_dir = STORAGE_DIR / "saved_fasta" / str(current_user.id)
        user_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{uuid.uuid4().hex}.fasta"
        file_path = user_dir / filename
        header = body.header or body.accession
        fasta_content = f">{header}\n{body.sequence}\n"
        file_path.write_text(fasta_content, encoding="utf-8")
        return {"success": True, "message": "Séquence sauvegardée", "file_id": filename}
    except OSError:
        raise HTTPException(500, "Erreur lors de la sauvegarde")