"""Tests d'intégration Nexora v2 — endpoints critiques."""
import pytest

from app.models.user import LoginAttempt

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
        json={"email": "test@nexora.test", "password": "password123"},
        headers={"X-Real-IP": "198.51.100.10"})
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
        json={"email": "me@nexora.test", "password": "password123"},
        headers={"X-Real-IP": "198.51.100.11"})
    assert login.status_code == 200
    token = login.json()["access_token"]
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == "me@nexora.test"

def test_login_rate_limit_is_scoped_by_identifier(client, db):
    ip = "198.51.100.99"
    victim_email = "locked@nexora.test"
    healthy_email = "healthy@nexora.test"
    db.query(LoginAttempt).filter(LoginAttempt.ip_address == ip).delete(synchronize_session=False)
    db.query(LoginAttempt).filter(
        LoginAttempt.identifier.in_([victim_email, healthy_email])
    ).delete(synchronize_session=False)
    db.commit()

    for username, email in [
        ("lockeduser", victim_email),
        ("healthyuser", healthy_email),
    ]:
        created = client.post("/api/v1/auth/register", json={
            "username": username, "email": email, "password": "password123"
        })
        assert created.status_code == 201

    for _ in range(5):
        failed = client.post("/api/v1/auth/login",
            json={"email": victim_email, "password": "wrong-password"},
            headers={"X-Real-IP": ip})
        assert failed.status_code == 401

    # The attacked identifier is rate-limited, but the same IP can still
    # authenticate a different legitimate account (shared NAT/proxy case).
    blocked_account = client.post("/api/v1/auth/login",
        json={"email": victim_email, "password": "password123"},
        headers={"X-Real-IP": ip})
    assert blocked_account.status_code == 429

    healthy_login = client.post("/api/v1/auth/login",
        json={"email": healthy_email, "password": "password123"},
        headers={"X-Real-IP": ip})
    assert healthy_login.status_code == 200
    assert "access_token" in healthy_login.json()


def test_login_ip_limit_is_applied_after_twenty_failures(client, db):
    ip = "198.51.100.100"
    target_email = "ip-limited@nexora.test"
    db.query(LoginAttempt).filter(LoginAttempt.ip_address == ip).delete(synchronize_session=False)
    db.query(LoginAttempt).filter(LoginAttempt.identifier == target_email).delete(synchronize_session=False)
    db.commit()

    created = client.post("/api/v1/auth/register", json={
        "username": "iplimiteduser", "email": target_email, "password": "password123"
    })
    assert created.status_code == 201

    for index in range(20):
        failed = client.post("/api/v1/auth/login",
            json={"email": f"unknown-{index}@nexora.test", "password": "wrong-password"},
            headers={"X-Real-IP": ip})
        assert failed.status_code == 401

    blocked_by_ip = client.post("/api/v1/auth/login",
        json={"email": target_email, "password": "password123"},
        headers={"X-Real-IP": ip})
    assert blocked_by_ip.status_code == 429

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
