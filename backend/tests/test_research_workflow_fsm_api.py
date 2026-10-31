def _token(client, username, email):
    client.post("/api/v1/auth/register", json={
        "username": username, "email": email, "password": "password123"
    })
    login = client.post("/api/v1/auth/login", data={
        "username": email, "password": "password123"
    }, headers={"Content-Type": "application/x-www-form-urlencoded"})
    return login.json()["access_token"]


def test_workflow_transitions_enforce_readiness_and_are_idempotent(client):
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
    assert blocked.status_code == 409
    assert "blockers" in blocked.json()["detail"]

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

    approved = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=headers,
        json={"action": "approve_data"},
    )
    assert approved.status_code == 200
    assert approved.json()["state"] == "analysis"

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
