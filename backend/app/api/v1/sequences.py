import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from app.database import get_db
from app.config import settings
from app.models.sequence import Analysis, Sequence, AnalysisStatus
from app.models.ncbi import NcbiSequence
from app.models.user import User
from app.services.auth_service import get_current_user
from app.core.sequence.translation import translate_dna_to_protein
from app.services.ncbi_client import (
    NCBIClientError,
    fetch_sequence as ncbi_fetch_sequence,
    search_sequences as ncbi_search_sequences,
    validate_accession as validate_ncbi_accession,
    validate_database as validate_ncbi_database,
)

router = APIRouter(prefix="/sequences", tags=["sequences"])

STORAGE_DIR = Path(settings.STORAGE_DIR)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
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
    try:
        accession_value = validate_ncbi_accession(accession)
        database_value = validate_ncbi_database(database)
    except NCBIClientError as exc:
        raise HTTPException(exc.status_code, str(exc)) from exc

    # Cache is scoped to the selected Entrez database and has a bounded TTL.
    if settings.NCBI_CACHE_TTL_SECONDS > 0:
        fresh_after = datetime.utcnow() - timedelta(seconds=settings.NCBI_CACHE_TTL_SECONDS)
        cached = (
            db.query(NcbiSequence)
            .filter(
                NcbiSequence.accession == accession_value,
                NcbiSequence.database_name == database_value,
                NcbiSequence.cached_at >= fresh_after,
            )
            .first()
        )
        if cached:
            return {
                "success": True,
                "data": {
                    "accession": cached.accession,
                    "sequence_data": cached.sequence,
                    "organism": cached.organism,
                    "gene_name": cached.title,
                    "length": cached.length,
                    "from_cache": True,
                    "database": cached.database_name,
                },
            }

    try:
        record = ncbi_fetch_sequence(accession_value, database_value)
    except NCBIClientError as exc:
        status = exc.status_code
        if status == 429 or status >= 500:
            status = 503 if status != 504 else 504
        raise HTTPException(status, str(exc)) from exc

    if record is None:
        raise HTTPException(404, "Aucune séquence FASTA trouvée pour cet identifiant NCBI.")
    if len(record["sequence_data"]) > MAX_SEQUENCE_LENGTH:
        raise HTTPException(
            413,
            f"Séquence NCBI trop longue pour cette route (maximum {MAX_SEQUENCE_LENGTH} caractères).",
        )

    # EFetch may return a versioned accession different from the query token.
    # Reuse an existing row when possible to avoid a unique-key collision.
    entry = (
        db.query(NcbiSequence)
        .filter(NcbiSequence.accession == record["accession"])
        .first()
    )
    if entry:
        entry.database_name = database_value
        entry.title = record["header"]
        entry.sequence = record["sequence_data"]
        entry.organism = record.get("organism")
        entry.length = record["length"]
        entry.cached_at = datetime.utcnow()
    else:
        entry = NcbiSequence(
            accession=record["accession"],
            database_name=database_value,
            title=record["header"],
            sequence=record["sequence_data"],
            organism=record.get("organism"),
            length=record["length"],
        )
        db.add(entry)
    db.commit()

    return {
        "success": True,
        "data": {
            "accession": record["accession"],
            "sequence_data": record["sequence_data"],
            "header": record["header"],
            "organism": record.get("organism"),
            "length": record["length"],
            "from_cache": False,
            "database": database_value,
        },
    }


@router.get("/ncbi")
def search_ncbi(
    query: str = Query(..., min_length=3, max_length=300),
    database: str = Query("nucleotide", pattern=r"^[A-Za-z0-9_]+$"),
    max_results: int = Query(10, ge=1, le=50),
    _: User = Depends(get_current_user),
):
    try:
        result = ncbi_search_sequences(query, database, max_results)
    except NCBIClientError as exc:
        status = exc.status_code
        if status == 429 or status >= 500:
            status = 503 if status != 504 else 504
        raise HTTPException(status, str(exc)) from exc

    return {
        "success": True,
        "results": result["results"],
        "total_count": result["total_count"],
    }


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