"""Curation services for African natural products in N3XORA-Sahel.

Imports never confer verified identity, verified plant occurrence, bioactivity,
or docking readiness. Those statuses require independent evidence and review.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import settings
from app.models.phytochemistry import PhytochemicalCandidate

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Lipinski, QED, rdMolDescriptors
    from rdkit.Chem import inchi as rd_inchi
except ImportError:  # RDKit is mandatory in the standard image, but fail explicitly if absent.
    Chem = None
    Descriptors = Lipinski = QED = rdMolDescriptors = rd_inchi = None

SOURCE_VALUES = {"ANPDB", "AFRODB", "WANPDB", "SAHEL_50", "MANUAL"}
SOURCE_LICENSES = {
    "ANPDB": "CC BY-NC 4.0 — attribution required; non-commercial use only; verify current source terms.",
    "AFRODB": "License/reuse terms must be checked against the original data record/publication before redistribution.",
    "WANPDB": "License/reuse terms not established here; verify the original publication/data provider.",
    "SAHEL_50": "User-provided list; redistribution rights/license not specified.",
    "MANUAL": "Not specified; curator must record the originating source and license.",
}
IMPORTED = "CANDIDAT_IMPORTÉ"
IDENTITY_VERIFIED = "IDENTITÉ_VÉRIFIÉE"
IDENTITY_REJECTED = "IDENTITÉ_REJETÉE"
OCCURRENCE_VERIFIED = "VÉRIFIÉE"


class CurationError(ValueError):
    pass


class ExternalReferenceError(RuntimeError):
    pass


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _pick(row: dict, *names: str) -> Optional[str]:
    lookup = {_norm_key(k): v for k, v in row.items()}
    for name in names:
        value = lookup.get(_norm_key(name))
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _rdkit_required():
    if Chem is None:
        raise CurationError("RDKit n'est pas disponible dans cet environnement.")


def _canonicalize(smiles: str) -> tuple[str, str, dict]:
    _rdkit_required()
    mol = Chem.MolFromSmiles(smiles or "")
    if mol is None:
        raise CurationError("SMILES invalide selon RDKit.")
    canonical = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
    key = rd_inchi.MolToInchiKey(mol) if rd_inchi else None
    descriptors = {
        "molecular_weight": round(float(Descriptors.MolWt(mol)), 5),
        "logp": round(float(Descriptors.MolLogP(mol)), 5),
        "hbd": int(Lipinski.NumHDonors(mol)),
        "hba": int(Lipinski.NumHAcceptors(mol)),
        "rotatable_bonds": int(Lipinski.NumRotatableBonds(mol)),
        "tpsa": round(float(Descriptors.TPSA(mol)), 5),
        "qed": round(float(QED.qed(mol)), 6),
        "formula": rdMolDescriptors.CalcMolFormula(mol),
    }
    return canonical, key or "", descriptors


from functools import lru_cache


@lru_cache(maxsize=1)
def _np_scorer_model():
    from rdkit.Contrib.NP_Score import npscorer
    return npscorer.readNPModel()


def _naturalness_score(mol) -> tuple[Optional[float], Optional[str]]:
    try:
        from rdkit.Contrib.NP_Score import npscorer
        scorer = _np_scorer_model()
        return float(npscorer.scoreMol(mol, scorer)), None
    except Exception as exc:
        return None, f"NP_Score RDKit non disponible: {type(exc).__name__}"


def compute_descriptors(candidate: PhytochemicalCandidate) -> dict:
    if not candidate.smiles_raw and not candidate.smiles_canonical:
        raise CurationError("Aucun SMILES n'est enregistré; aucun descripteur ne peut être calculé.")
    canonical, inchi_key, desc = _canonicalize(candidate.smiles_canonical or candidate.smiles_raw)
    mol = Chem.MolFromSmiles(canonical)
    naturalness, warning = _naturalness_score(mol)
    desc["natural_product_likeness"] = naturalness
    desc["natural_product_likeness_method"] = "RDKit Contrib NP_Score" if naturalness is not None else None
    candidate.smiles_canonical = canonical
    candidate.formula = desc["formula"]
    candidate.inchikey = candidate.inchikey or inchi_key or None
    candidate.descriptors = desc
    if warning:
        warnings = list(candidate.import_warnings or [])
        if warning not in warnings:
            warnings.append(warning)
        candidate.import_warnings = warnings
    return desc


def _create_candidate(db: Session, *, source: str, source_row: str, name: str,
                      plant: Optional[str] = None, chemical_class: Optional[str] = None,
                      raw_smiles: Optional[str] = None, cid: Optional[str] = None,
                      inchikey: Optional[str] = None, formula: Optional[str] = None,
                      synonyms: Optional[list] = None, citation: Optional[str] = None,
                      plant_part: Optional[str] = None, source_license: Optional[str] = None,
                      source_sha: Optional[str] = None, warnings: Optional[list] = None) -> tuple[PhytochemicalCandidate, bool]:
    if source not in SOURCE_VALUES:
        raise CurationError(f"Source de bibliothèque inconnue: {source}")
    name = (name or "").strip()
    if not name:
        raise CurationError("Une entrée sans nom ne peut pas être importée.")
    existing = db.query(PhytochemicalCandidate).filter(
        PhytochemicalCandidate.library_source == source,
        PhytochemicalCandidate.source_row == str(source_row),
        PhytochemicalCandidate.name_raw == name,
    ).first()
    if existing:
        return existing, False

    candidate = PhytochemicalCandidate(
        name_raw=name,
        synonyms=synonyms or [],
        pubchem_cid=int(cid) if cid and str(cid).isdigit() else None,
        inchikey=inchikey or None,
        smiles_raw=raw_smiles or None,
        formula=formula or None,
        source_plant=plant or None,
        plant_part=plant_part or None,
        occurrence_source=citation or None,
        library_source=source,
        source_license=source_license or SOURCE_LICENSES.get(source),
        source_file_sha256=source_sha,
        source_row=str(source_row),
        identity_status=IMPORTED,
        occurrence_status="A_VÉRIFIER",
        evidence_grade="D",
        chemical_class=chemical_class or None,
        import_warnings=warnings or [],
    )
    if candidate.smiles_raw:
        try:
            # This is format normalization only; identity is still unverified.
            candidate.smiles_canonical, _key, desc = _canonicalize(candidate.smiles_raw)
            candidate.formula = candidate.formula or desc["formula"]
            candidate.descriptors = desc
        except CurationError as exc:
            candidate.import_warnings = list(candidate.import_warnings or []) + [str(exc)]
    db.add(candidate)
    return candidate, True


def import_from_anpdb(csv_path: str, db: Session, max_import: Optional[int] = None) -> dict:
    path = Path(csv_path)
    if not path.is_file():
        raise CurationError("Fichier CSV ANPDB introuvable.")
    limit = max_import or settings.PHYTOCHEM_MAX_IMPORT
    digest = sha256_file(path)
    imported = skipped = 0
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        for row_number, row in enumerate(reader, start=2):
            if row_number > limit + 1:
                break
            # Current ANPDB bulk CSV includes mol_name, molecule_id, smiles,
            # class_name/subclass_name, species and part_org.
            name = _pick(row, "mol_name", "name", "compound name", "compound", "natural product", "title", "chemical name")
            if not name:
                skipped += 1
                continue
            source_id = _pick(row, "molecule_id", "anpdb_id", "compound_id")
            _candidate, created = _create_candidate(
                db, source="ANPDB", source_row=source_id or str(row_number), name=name,
                plant=_pick(row, "species_most_recent_name", "species", "source organism", "organism", "plant", "source species"),
                plant_part=_pick(row, "part_org", "plant part", "part", "organ"),
                chemical_class=_pick(row, "class_name", "subclass_name", "chemical class", "class"),
                raw_smiles=_pick(row, "smiles", "canonical smiles", "isomeric smiles", "canonical_smiles"),
                cid=_pick(row, "pubchem_id", "pubchem cid", "cid", "pubchem compound id"),
                inchikey=_pick(row, "inchikey", "inchi key"),
                formula=_pick(row, "molecular_formula", "formula", "molecular formula"),
                synonyms=[x.strip() for x in re.split(r"[;|]", _pick(row, "synonyms") or "") if x.strip()],
                citation=_pick(row, "reference_title", "reference", "doi", "pmid", "publication", "citation"),
                source_sha=digest,
            )
            imported += int(created)
            skipped += int(not created)
    db.commit()
    return {"source": "ANPDB", "imported": imported, "skipped_or_duplicate": skipped,
            "source_file_sha256": digest, "status": IMPORTED, "limit": limit}


def import_from_afrodb(sdf_path: str, db: Session, max_import: Optional[int] = None) -> dict:
    _rdkit_required()
    path = Path(sdf_path)
    if not path.is_file():
        raise CurationError("Fichier SDF AfroDb introuvable.")
    limit = max_import or settings.PHYTOCHEM_MAX_IMPORT
    digest = sha256_file(path)
    supplier = Chem.SDMolSupplier(str(path), removeHs=False, sanitize=True)
    imported = skipped = invalid = 0
    for row_number, mol in enumerate(supplier, start=1):
        if row_number > limit:
            break
        if mol is None:
            invalid += 1
            continue
        props = {name: mol.GetProp(name) for name in mol.GetPropNames()}
        name = _pick(props, "PUBCHEM_IUPAC_TRADITIONAL_NAME", "compound name", "name", "title",
                     "PUBCHEM_IUPAC_NAME", "CHEMICAL NAME")
        if not name:
            name = _pick(props, "PUBCHEM_COMPOUND_CID", "CID") or f"AfroDb import row {row_number}"
        smiles = Chem.MolToSmiles(mol, canonical=True, isomericSmiles=True)
        canonical, key, desc = _canonicalize(smiles)
        candidate, created = _create_candidate(
            db, source="AFRODB", source_row=str(row_number), name=name,
            plant=_pick(props, "source organism", "organism", "species", "plant"),
            chemical_class=_pick(props, "chemical class", "class"),
            raw_smiles=smiles,
            cid=_pick(props, "PUBCHEM_COMPOUND_CID", "PUBCHEM_CID", "CID"),
            inchikey=_pick(props, "PUBCHEM_IUPAC_INCHIKEY", "PUBCHEM_CACTVS_INCHIKEY", "INCHIKEY"),
            formula=_pick(props, "PUBCHEM_CACTVS_MOLECULAR_FORMULA", "MOLECULAR FORMULA"),
            citation=_pick(props, "reference", "doi", "pmid", "citation"),
            source_sha=digest,
        )
        if created:
            candidate.smiles_canonical = canonical
            candidate.inchikey = candidate.inchikey or key or None
            candidate.descriptors = desc
        imported += int(created)
        skipped += int(not created)
    db.commit()
    return {"source": "AFRODB", "imported": imported, "skipped_or_duplicate": skipped,
            "invalid_sdf_records": invalid, "source_file_sha256": digest,
            "status": IMPORTED, "limit": limit}


def import_from_wanpdb(csv_path: str, db: Session, max_import: Optional[int] = None) -> dict:
    """Import a user-supplied WANPDB CSV while preserving source file and row provenance.

    WANPDB input is not downloaded automatically because a stable public API/file
    endpoint has not been established here. Every row remains a candidate.
    """
    path = Path(csv_path)
    if not path.is_file():
        raise CurationError("Fichier CSV WANPDB introuvable.")
    limit = max_import or settings.PHYTOCHEM_MAX_IMPORT
    digest = sha256_file(path)
    imported = skipped = 0
    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as handle:
        sample = handle.read(8192)
        handle.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\\t")
        except csv.Error:
            dialect = csv.excel
        reader = csv.DictReader(handle, dialect=dialect)
        for row_number, row in enumerate(reader, start=2):
            if row_number > limit + 1:
                break
            name = _pick(row, "mol_name", "name", "compound name", "compound", "natural product", "title", "chemical name")
            if not name:
                skipped += 1
                continue
            source_id = _pick(row, "molecule_id", "compound_id", "id")
            _candidate, created = _create_candidate(
                db, source="WANPDB", source_row=source_id or str(row_number), name=name,
                plant=_pick(row, "species", "source organism", "organism", "plant", "source species", "taxon"),
                plant_part=_pick(row, "part_org", "plant part", "part", "organ"),
                chemical_class=_pick(row, "class_name", "subclass_name", "chemical class", "class", "compound class"),
                raw_smiles=_pick(row, "smiles", "canonical smiles", "isomeric smiles", "canonical_smiles"),
                cid=_pick(row, "pubchem_id", "pubchem cid", "cid", "pubchem compound id"),
                inchikey=_pick(row, "inchikey", "inchi key"),
                formula=_pick(row, "molecular_formula", "formula", "molecular formula"),
                synonyms=[x.strip() for x in re.split(r"[;|]", _pick(row, "synonyms") or "") if x.strip()],
                citation=_pick(row, "reference_title", "reference", "doi", "pmid", "publication", "citation"),
                source_sha=digest,
            )
            imported += int(created)
            skipped += int(not created)
    db.commit()
    return {"source": "WANPDB", "imported": imported, "skipped_or_duplicate": skipped,
            "source_file_sha256": digest, "status": IMPORTED, "limit": limit}


def import_sahel_50(json_path: Optional[str], db: Session) -> dict:
    path = Path(json_path) if json_path else Path(__file__).resolve().parents[1] / "fixtures" / "sahel_50_ligands.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset") != "SAHEL_50" or len(payload.get("records", [])) != 50:
        raise CurationError("Le seed Sahel doit contenir exactement 50 entrées source.")
    imported = skipped = 0
    for record in payload["records"]:
        # Do not populate any chemical identity, taxonomy or bioactivity fields not present in source.
        if record.get("smiles_raw") or record.get("smiles_canonical") or record.get("pubchem_cid") or record.get("inchikey"):
            raise CurationError(f"Le seed source contient une identité chimique non autorisée: ligne {record.get('source_row')}")
        _candidate, created = _create_candidate(
            db, source="SAHEL_50", source_row=str(record["source_row"]),
            name=record["source_name"], plant=record.get("source_plant"),
            chemical_class=record.get("source_class"), source_sha=sha256_file(path),
            warnings=record.get("import_warnings") or [],
        )
        imported += int(created)
        skipped += int(not created)
    db.commit()
    return {"source": "SAHEL_50", "imported": imported, "skipped_or_duplicate": skipped,
            "total_source_records": 50, "status": IMPORTED}


def _norm_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").casefold())


def verify_identity(candidate: PhytochemicalCandidate, pubchem_cid: int,
                    inchikey: str, smiles: str) -> dict:
    """Cross-check user-provided identity against PubChem, including the candidate name."""
    if not pubchem_cid or not inchikey or not smiles:
        raise CurationError("CID PubChem, InChIKey et SMILES doivent tous être fournis.")
    canonical_input, input_key, _ = _canonicalize(smiles)
    if input_key.casefold() != inchikey.strip().casefold():
        raise CurationError("Le SMILES fourni ne correspond pas à l'InChIKey fourni.")

    base = settings.PUBCHEM_API_URL.rstrip("/")
    try:
        response = httpx.get(
            f"{base}/compound/cid/{int(pubchem_cid)}/property/CanonicalSMILES,IsomericSMILES,InChIKey,MolecularFormula/JSON",
            timeout=20.0, headers={"User-Agent": "N3XORA-Sahel-BioEvidence/1.0"}
        )
        response.raise_for_status()
        props = response.json()["PropertyTable"]["Properties"][0]
        synonyms_response = httpx.get(
            f"{base}/compound/cid/{int(pubchem_cid)}/synonyms/JSON",
            timeout=20.0, headers={"User-Agent": "N3XORA-Sahel-BioEvidence/1.0"}
        )
        synonyms_response.raise_for_status()
        synonyms_data = synonyms_response.json()["InformationList"]["Information"][0].get("Synonym", [])
    except Exception as exc:
        raise ExternalReferenceError(f"PubChem indisponible ou réponse invalide: {type(exc).__name__}") from exc

    pubchem_key = str(props.get("InChIKey") or "")
    pubchem_smiles = str(props.get("ConnectivitySMILES") or props.get("CanonicalSMILES") or props.get("SMILES") or "")
    # PubChem currently returns ConnectivitySMILES for CanonicalSMILES on PUG REST;
    # the external InChIKey is used as the stable identity comparison.
    if not pubchem_key:
        raise ExternalReferenceError("PubChem n'a pas fourni d'InChIKey pour ce CID.")
    if pubchem_key.casefold() != inchikey.strip().casefold() or input_key.casefold() != pubchem_key.casefold():
        candidate.identity_status = IDENTITY_REJECTED
        candidate.review_note = (
            f"Identité refusée le {datetime.utcnow().isoformat()} UTC: le CID/InChIKey/SMILES ne concordent pas."
        )
        return {"verified": False, "identity_status": IDENTITY_REJECTED, "reason": "identity_mismatch"}

    aliases = {_norm_name(str(item)) for item in synonyms_data}
    if _norm_name(candidate.name_raw) not in aliases:
        candidate.identity_status = IDENTITY_REJECTED
        candidate.review_note = (
            f"Identité refusée le {datetime.utcnow().isoformat()} UTC: le nom source ne figure pas dans les synonymes PubChem du CID fourni."
        )
        return {"verified": False, "identity_status": IDENTITY_REJECTED, "reason": "candidate_name_not_in_pubchem_synonyms",
                "pubchem_cid": int(pubchem_cid)}

    # Use the externally cross-checked structure as the canonical structure.
    candidate.pubchem_cid = int(pubchem_cid)
    candidate.inchikey = pubchem_key
    candidate.smiles_raw = smiles.strip()
    candidate.smiles_canonical = canonical_input
    candidate.formula = str(props.get("MolecularFormula") or "")
    candidate.canonical_name = candidate.name_raw
    candidate.identity_status = IDENTITY_VERIFIED
    candidate.reviewed_at = datetime.utcnow()
    candidate.review_note = f"Identité comparée au CID PubChem {pubchem_cid}, SMILES/InChIKey concordants."
    desc = compute_descriptors(candidate)
    return {"verified": True, "identity_status": IDENTITY_VERIFIED, "pubchem_cid": int(pubchem_cid),
            "inchikey": pubchem_key, "smiles_canonical": candidate.smiles_canonical,
            "formula": candidate.formula, "descriptors": desc,
            "source_url": f"https://pubchem.ncbi.nlm.nih.gov/compound/{int(pubchem_cid)}"}


def verify_occurrence(candidate: PhytochemicalCandidate, taxon: str, reference: str,
                      attest_compound_occurrence: bool, curator: str, review_note: str) -> dict:
    """Resolve accepted taxon through GBIF and require explicit human evidence attestation.

    GBIF confirms taxonomy; it does not prove that a compound occurs in a plant.
    The cited publication/source and curator attestation address that separate claim.
    """
    if not taxon or not reference or not review_note or not curator or not attest_compound_occurrence:
        raise CurationError(
            "Taxon, DOI/PMID/URL, identité du curateur, note de revue et attestation explicite de l'occurrence sont requis."
        )
    try:
        response = httpx.get("https://api.gbif.org/v1/species/match",
                             params={"name": taxon, "strict": "true"},
                             timeout=20.0, headers={"User-Agent": "N3XORA-Sahel-BioEvidence/1.0"})
        response.raise_for_status()
        match = response.json()
    except Exception as exc:
        raise ExternalReferenceError(f"GBIF indisponible ou réponse invalide: {type(exc).__name__}") from exc

    usage_key = match.get("usageKey") or match.get("acceptedUsageKey")
    rank = str(match.get("rank") or "").upper()
    status = str(match.get("status") or "").upper()
    if not usage_key or rank != "SPECIES" or status not in {"ACCEPTED", "DOUBTFUL"}:
        raise CurationError("GBIF n'a pas fourni de correspondance taxonomique au rang espèce acceptable.")
    ref = reference.strip()
    doi_or_pmid = bool(re.search(r"(10\.\d{4,9}/[^\s]+|\bPMID\s*:?\s*\d+\b|pubmed\.ncbi\.nlm\.nih\.gov/\d+)", ref, re.I))
    if not (doi_or_pmid or ref.lower().startswith(("https://", "http://"))):
        raise CurationError("La source doit être un DOI, un PMID ou une URL vérifiable.")

    candidate.accepted_taxon = str(match.get("scientificName") or taxon)
    candidate.taxon_identifier = f"GBIF:{usage_key}"
    candidate.occurrence_source = ref
    candidate.occurrence_status = OCCURRENCE_VERIFIED
    candidate.curator = curator.strip()
    candidate.reviewed_at = datetime.utcnow()
    candidate.review_note = review_note.strip()
    warnings = list(candidate.import_warnings or [])
    warning = "Occurrence botanique attestée par un curateur; la référence doit être conservée et auditée."
    if warning not in warnings:
        warnings.append(warning)
    candidate.import_warnings = warnings
    return {"verified": True, "occurrence_status": OCCURRENCE_VERIFIED,
            "accepted_taxon": candidate.accepted_taxon, "taxon_identifier": candidate.taxon_identifier,
            "occurrence_source": ref, "curator": candidate.curator,
            "note": "GBIF a résolu le taxon; l'occurrence composé-plante repose sur l'attestation humaine citée, pas sur GBIF seul."}


def ready_for_docking(candidate: PhytochemicalCandidate) -> tuple[bool, list[str]]:
    blockers = []
    if candidate.identity_status != IDENTITY_VERIFIED:
        blockers.append("identité chimique non vérifiée")
    if candidate.occurrence_status != OCCURRENCE_VERIFIED:
        blockers.append("occurrence botanique non vérifiée par curateur")
    if not candidate.inchikey:
        blockers.append("InChIKey absent")
    if not candidate.smiles_canonical:
        blockers.append("SMILES canonique absent")
    else:
        try:
            _canonicalize(candidate.smiles_canonical)
        except CurationError:
            blockers.append("SMILES canonique invalide")
    if not candidate.accepted_taxon or not candidate.occurrence_source:
        blockers.append("taxon accepté ou référence botanique absent")
    return (not blockers, blockers)


def candidate_to_dict(candidate: PhytochemicalCandidate, include_descriptors: bool = True) -> dict:
    result = {
        "id": candidate.id, "name_raw": candidate.name_raw, "canonical_name": candidate.canonical_name,
        "synonyms": candidate.synonyms or [], "pubchem_cid": candidate.pubchem_cid,
        "inchikey": candidate.inchikey, "smiles_raw": candidate.smiles_raw,
        "smiles_canonical": candidate.smiles_canonical, "formula": candidate.formula,
        "stereochemistry": candidate.stereochemistry, "source_plant": candidate.source_plant,
        "accepted_taxon": candidate.accepted_taxon, "taxon_identifier": candidate.taxon_identifier,
        "plant_part": candidate.plant_part, "occurrence_source": candidate.occurrence_source,
        "occurrence_status": candidate.occurrence_status, "library_source": candidate.library_source,
        "source_file_sha256": candidate.source_file_sha256, "source_row": candidate.source_row,
        "source_license": candidate.source_license,
        "identity_status": candidate.identity_status, "evidence_grade": candidate.evidence_grade,
        "chemical_class": candidate.chemical_class, "target_organism": candidate.target_organism,
        "target_accession": candidate.target_accession, "assay_type": candidate.assay_type,
        "endpoint_value": candidate.endpoint_value, "endpoint_unit": candidate.endpoint_unit,
        "study_reference": candidate.study_reference, "curator": candidate.curator,
        "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
        "review_note": candidate.review_note, "import_warnings": candidate.import_warnings or [],
        "ready_for_docking": ready_for_docking(candidate)[0],
        "docking_blockers": ready_for_docking(candidate)[1],
    }
    if include_descriptors:
        result["descriptors"] = candidate.descriptors or {}
    return result
