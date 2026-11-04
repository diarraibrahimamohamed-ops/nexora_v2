def _token(client, username, email):
    client.post("/api/v1/auth/register", json={
        "username": username, "email": email, "password": "password123"
    })
    login = client.post("/api/v1/auth/token", data={
        "username": email, "password": "password123"
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    return login.json()["access_token"]


def test_workflow_transitions_enforce_readiness_and_independent_review(client, db):
    token = _token(client, "fsm_user", "fsm-user@nexora.test")
    headers = {"Authorization": f"Bearer {token}"}
    created = client.post("/api/v1/research/studies", headers=headers, json={
        "name": "Étude FSM",
        "disease": "malaria",
        "pathogen": "Plasmodium falciparum",
        "country_focus": "Mali",
        "region_scope": "Sahel",
        "objective": "Étudier une cible protéique.",
        "hypothesis": "Une variation peut modifier localement la structure.",
    })
    assert created.status_code == 200
    study_id = created.json()["study"]["id"]

    first = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers={**headers, "Idempotency-Key": "submit-fsm-review-1"},
        json={"action": "submit_for_review"},
    )
    assert first.status_code == 200
    assert first.json()["state"] == "data_review"

    replay = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers={**headers, "Idempotency-Key": "submit-fsm-review-1"},
        json={"action": "submit_for_review"},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True

    blocked = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "approve_data"},
    )
    assert blocked.status_code == 403

    target = client.post(
        f"/api/v1/research/studies/{study_id}/targets",
        headers=headers,
        json={
            "state": "wt",
            "protein_name": "Test target",
            "protein_sequence": "MKTAYIAKQRQISFVKSHFSRQ",
            "structure_source": "sequence_resolved",
        },
    )
    assert target.status_code == 200
    target_id = target.json()["target_id"]

    premature_structure = client.post(
        f"/api/v1/research/studies/{study_id}/targets/{target_id}/resolve-structure",
        headers=headers,
        json={"provider": "auto_rcsb"},
    )
    assert premature_structure.status_code == 409
    assert premature_structure.json()["detail"]["required_state"] == "analysis"

    owner_self_approval = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "approve_data"},
    )
    assert owner_self_approval.status_code == 403

    reviewer_token = _token(client, "fsm_reviewer", "fsm-reviewer@nexora.test")
    from app.models.user import User
    reviewer = db.query(User).filter(User.email == "fsm-reviewer@nexora.test").first()
    reviewer.is_admin = True
    db.commit()
    review = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/review-data",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={"reason": "Contrôle indépendant des données et des identifiants de cible."},
    )
    assert review.status_code == 200
    assert review.json()["state"] == "analysis"

    no_results = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "submit_results"},
    )
    assert no_results.status_code == 409

    blocked = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "block", "reason": "Vérification externe en attente."},
    )
    assert blocked.status_code == 200
    assert blocked.json()["state"] == "blocked"

    resumed = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "resume", "reason": "Vérification terminée."},
    )
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "analysis"

    timeline = client.get(
        f"/api/v1/research/studies/{study_id}/workflow",
        headers=headers,
    )
    assert timeline.status_code == 200
    assert timeline.json()["state"] == "analysis"
    assert len(timeline.json()["events"]) == 5
    assert timeline.json()["events"][0]["action"] == "created"
    assert timeline.json()["events"][0]["to_state"] == "draft"



def test_research_evidence_claim_requires_independent_active_admin_review(client, db):
    owner_token = _token(client, "evidence_owner", "evidence-owner@nexora.test")
    owner_headers = {"Authorization": f"Bearer {owner_token}"}
    study_response = client.post("/api/v1/research/studies", headers=owner_headers, json={
        "name": "Dossier preuve",
        "objective": "Évaluer une observation publiée.",
        "hypothesis": "Une relation publiée est reproductible.",
    })
    assert study_response.status_code == 200
    study_id = study_response.json()["study"]["id"]

    declared = client.post(
        f"/api/v1/research/studies/{study_id}/evidence",
        headers=owner_headers,
        json={
            "object_type": "publication",
            "evidence_kind": "experimental_report",
            "evidence_status": "experimentally_validated",
            "evidence_grade": "B",
            "claim": "La publication rapporte un résultat expérimental pour la cible étudiée.",
            "source_url": "https://doi.org/10.1234/example",
            "method": "reported_assay",
            "tool_name": "source_publication",
        },
    )
    assert declared.status_code == 201
    evidence_id = declared.json()["evidence"]["id"]
    assert declared.json()["evidence"]["review_status"] == "pending_review"
    assert declared.json()["evidence"]["qualified_evidence_status"] == "unreviewed_claim"

    from app.models.user import User
    owner = db.query(User).filter(User.email == "evidence-owner@nexora.test").first()
    owner.is_admin = True
    db.commit()
    self_review = client.post(
        f"/api/v1/research/studies/{study_id}/evidence/{evidence_id}/review",
        headers=owner_headers,
        json={"decision": "accepted", "reason": "Je confirme ma propre preuve."},
    )
    assert self_review.status_code == 403

    reviewer_token = _token(client, "evidence_reviewer", "evidence-reviewer@nexora.test")
    reviewer = db.query(User).filter(User.email == "evidence-reviewer@nexora.test").first()
    reviewer.is_admin = True
    db.commit()
    reviewed = client.post(
        f"/api/v1/research/studies/{study_id}/evidence/{evidence_id}/review",
        headers={"Authorization": f"Bearer {reviewer_token}"},
        json={"decision": "accepted", "reason": "Référence et nature de l'essai contrôlées indépendamment."},
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["evidence"]["review_status"] == "accepted"
    assert reviewed.json()["evidence"]["qualified_evidence_status"] == "experimentally_validated"
    assert reviewed.json()["evidence"]["reviewed_by"] == reviewer.id
