"""Tests d'intégration Nexora v2 — endpoints critiques."""
import pytest

def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

def test_register_and_login(client):
    r = client.post("/api/v1/auth/register", json={
        "username": "testuser", "email": "test@nexora.test", "password": "password123"
    })
    assert r.status_code == 201

    r = client.post("/api/v1/auth/login",
        data={"username": "test@nexora.test", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    assert "access_token" in r.json()
    return r.json()["access_token"]

def test_register_duplicate(client):
    client.post("/api/v1/auth/register", json={
        "username": "dupuser", "email": "dup@nexora.test", "password": "password123"
    })
    r = client.post("/api/v1/auth/register", json={
        "username": "dupuser2", "email": "dup@nexora.test", "password": "password123"
    })
    assert r.status_code == 400

def test_auth_me(client):
    client.post("/api/v1/auth/register", json={
        "username": "meuser", "email": "me@nexora.test", "password": "password123"
    })
    login = client.post("/api/v1/auth/login",
        data={"username": "me@nexora.test", "password": "password123"},
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    token = login.json()["access_token"]
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "me@nexora.test"

def test_analyze_sequence_dna(client):
    r = client.post("/api/v1/analysis/sequence",
        json={"sequence": "ATGCGATCGATCGATCGATCG"})
    assert r.status_code == 200
    data = r.json()
    assert "fragments" in data
    assert data["input_length"] == 21
    assert data["fragments"][0]["gc_percent"] > 0

def test_analyze_sequence_empty(client):
    r = client.post("/api/v1/analysis/sequence", json={"sequence": "XXXXX"})
    assert r.status_code == 400

def test_translate_dna(client):
    r = client.post("/api/v1/sequences/translate",
        json={"dna_sequence": "ATGAAAGCGATTTAA"})
    assert r.status_code == 200
    assert "protein_sequence" in r.json()

def test_ligands_list(client):
    r = client.get("/api/v1/ligands")
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert isinstance(data["ligands"], list)

def test_ligands_categories(client):
    r = client.get("/api/v1/ligands/categories")
    assert r.status_code == 200

def test_ligand_search(client):
    r = client.get("/api/v1/ligands/search?q=aspirin")
    assert r.status_code == 200

def test_docking_submit_unauth(client):
    r = client.post("/api/v1/docking/submit", json={
        "protein_sequence": "MKAIFVLKGT", "ligand_smiles": "CC(=O)Nc1ccc(O)cc1"
    })
    assert r.status_code == 401

def test_analysis_history_unauth(client):
    r = client.get("/api/v1/analysis/history")
    assert r.status_code == 401
