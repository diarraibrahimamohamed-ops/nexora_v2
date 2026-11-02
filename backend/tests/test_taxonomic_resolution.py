"""Tests for WCVP-backed botanical name-resolution proposals and review gates."""
import pytest
from app.config import settings
from app.models.phytochemistry import PhytochemicalCandidate
from app.services.phytochemistry_catalog import CurationError, IMPORTED, OCCURRENCE_VERIFIED, verify_occurrence
from app.services.taxonomic_resolution import ACCEPTABLE_PLANT_RANKS, WCVP_CHECKLIST_KEY, TaxonomyResolutionError, resolve_botanical_taxon, review_taxon_resolution


class FakeResponse:
    def __init__(self,payload): self.payload=payload
    def raise_for_status(self): return None
    def json(self): return self.payload


def wcvp_payload(kingdom="Plantae"):
    return {"usage":{"key":"syn-1","name":"Acacia mollissima Willd.","canonicalName":"Acacia mollissima","rank":"SPECIES","status":"SYNONYM"},
            "acceptedUsage":{"key":"acc-1","name":"Acacia mearnsii De Wild.","canonicalName":"Acacia mearnsii","rank":"SPECIES","status":"ACCEPTED"},
            "classification":[{"name":kingdom,"rank":"KINGDOM"},{"name":"Fabaceae","rank":"FAMILY"}],
            "diagnostics":{"matchType":"EXACT","confidence":99}}


def candidate_row(db):
    row=PhytochemicalCandidate(name_raw="Caffeine",library_source="MANUAL",source_row="taxonomy-test",
                               source_plant="Acacia mollissima",identity_status=IMPORTED)
    db.add(row); db.commit(); db.refresh(row); return row


def test_explicit_wcvp_checklist_and_synonym_preservation(monkeypatch,db,tmp_path):
    monkeypatch.setattr(settings,"AFRICAN_LIBS_DIR",str(tmp_path))
    seen={}
    def fake_get(url,*,params,timeout,headers):
        seen.update(url=url,params=params); return FakeResponse(wcvp_payload())
    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get",fake_get)
    candidate=candidate_row(db); row=resolve_botanical_taxon(db,"Acacia mollissima",candidate_id=candidate.id)
    db.commit(); db.refresh(row)
    assert seen["url"].endswith("/v2/species/match")
    assert seen["params"]["checklistKey"]==WCVP_CHECKLIST_KEY and seen["params"]["kingdom"]=="Plantae"
    assert row.matched_status=="SYNONYM" and row.accepted_name=="Acacia mearnsii De Wild."
    assert row.review_status=="pending_review" and len(row.response_sha256)==64
    assert row.dataset_doi=="10.15468/6h8ucr"
    assert candidate.accepted_taxon is None and candidate.occurrence_status!=OCCURRENCE_VERIFIED


def test_accept_updates_taxon_only_not_occurrence(monkeypatch,db,tmp_path):
    monkeypatch.setattr(settings,"AFRICAN_LIBS_DIR",str(tmp_path))
    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get",lambda *a,**k:FakeResponse(wcvp_payload()))
    candidate=candidate_row(db); row=resolve_botanical_taxon(db,"Acacia mollissima",candidate_id=candidate.id); db.commit()
    review_taxon_resolution(db,row,decision="accept",reviewer="curator@example.org",
                            rationale="Synonym and accepted name reviewed against WCVP record.")
    db.commit(); db.refresh(candidate)
    assert row.review_status=="accepted" and candidate.accepted_taxon=="Acacia mearnsii De Wild."
    assert candidate.taxon_identifier=="WCVP:acc-1"
    assert candidate.taxonomy_resolution_source=="WCVP_VIA_GBIF_CHECKLISTBANK"
    assert candidate.identity_status==IMPORTED and candidate.occurrence_status=="A_VÉRIFIER"


def test_occurrence_requires_accepted_taxon_and_human_source(db):
    candidate=candidate_row(db)
    with pytest.raises(CurationError):
        verify_occurrence(candidate,"Acacia mearnsii","https://doi.org/10.1234/example",True,
                          "curator","Primary source checked for compound occurrence.")
    candidate.accepted_taxon="Acacia mearnsii De Wild."; candidate.taxon_identifier="WCVP:acc-1"
    candidate.taxonomy_resolution_source="WCVP_VIA_GBIF_CHECKLISTBANK"; candidate.taxonomy_cache_sha256="a"*64
    result=verify_occurrence(candidate,"Acacia mearnsii De Wild.","https://doi.org/10.1234/example",True,
                             "curator","Primary source checked for compound occurrence.")
    assert result["verified"] and candidate.occurrence_status==OCCURRENCE_VERIFIED


def test_non_plant_result_cannot_be_accepted(monkeypatch,db,tmp_path):
    monkeypatch.setattr(settings,"AFRICAN_LIBS_DIR",str(tmp_path))
    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get",lambda *a,**k:FakeResponse(wcvp_payload("Animalia")))
    candidate=candidate_row(db); row=resolve_botanical_taxon(db,"Acacia mollissima",candidate_id=candidate.id)
    assert row.review_status=="unresolved" and row.accepted_name is None
    with pytest.raises(TaxonomyResolutionError):
        review_taxon_resolution(db,row,decision="accept",reviewer="curator",rationale="Non-plant result must be rejected.")
    assert candidate.accepted_taxon is None


def test_genus_rank_result_cannot_be_accepted(monkeypatch,db,tmp_path):
    monkeypatch.setattr(settings,"AFRICAN_LIBS_DIR",str(tmp_path))
    data=wcvp_payload(); data["acceptedUsage"]["rank"]="GENUS"
    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get",lambda *a,**k:FakeResponse(data))
    candidate=candidate_row(db); row=resolve_botanical_taxon(db,"Acacia",candidate_id=candidate.id)
    assert "SPECIES" in ACCEPTABLE_PLANT_RANKS
    with pytest.raises(TaxonomyResolutionError):
        review_taxon_resolution(db,row,decision="accept",reviewer="curator",rationale="Genus-level result cannot be accepted.")


def test_exact_query_cache_reused_offline(monkeypatch,db,tmp_path):
    monkeypatch.setattr(settings,"AFRICAN_LIBS_DIR",str(tmp_path)); calls={"n":0}
    def fake_get(*a,**k):
        calls["n"]+=1
        if calls["n"]>1: raise OSError("offline")
        return FakeResponse(wcvp_payload())
    monkeypatch.setattr("app.services.taxonomic_resolution.httpx.get",fake_get)
    candidate=candidate_row(db)
    first=resolve_botanical_taxon(db,"Acacia mollissima",candidate_id=candidate.id)
    second=resolve_botanical_taxon(db,"Acacia mollissima",candidate_id=candidate.id)
    assert first.source_mode=="LIVE" and second.source_mode=="CACHE"
    assert first.response_sha256==second.response_sha256
