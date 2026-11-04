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

    from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun, ScientificWorkflowStep
    from app.models.workflow import ResearchWorkflowEvent
    initial_run = db.query(ScientificWorkflowRun).filter_by(
        workflow_key="nexora.research-study-lifecycle",
        scope_type="research_study",
        scope_id=str(study_id),
    ).one()
    assert initial_run.state == "draft"
    assert initial_run.state_version == 0
    assert initial_run.definition_snapshot["state_only"] is True
    assert db.query(ScientificWorkflowStep).filter_by(workflow_run_id=initial_run.id).count() == 0
    created_import = db.query(ScientificWorkflowEvent).filter_by(
        workflow_run_id=initial_run.id,
        event_type="legacy_transition_imported",
    ).one()
    assert created_import.payload["legacy_event_id"] == db.query(ResearchWorkflowEvent).filter_by(
        study_id=study_id, action="created"
    ).one().id

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
    canonical = timeline.json()["scientific_workflow"]
    assert canonical["workflow_key"] == "nexora.research-study-lifecycle"
    assert canonical["state"] == "analysis"
    assert canonical["state_only"] is True
    assert canonical["step_count"] == 0

    from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun, ScientificWorkflowStep
    generic_mutation = client.post(
        f"/api/v1/scientific-workflows/{canonical['run_id']}/actions",
        headers=headers,
        json={
            "action": "block",
            "reason": "Research state must be changed through the Research API.",
            "expected_state_version": canonical["state_version"],
        },
    )
    assert generic_mutation.status_code == 409
    db.refresh(run := db.query(ScientificWorkflowRun).filter_by(id=canonical["run_id"]).one())
    assert run.state == "analysis"
    assert run.state_version == canonical["state_version"]
    run = db.query(ScientificWorkflowRun).filter_by(id=canonical["run_id"]).one()
    assert run.state_version == 4
    assert db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).count() == 0
    hfsm_events = db.query(ScientificWorkflowEvent).filter_by(workflow_run_id=run.id).all()
    assert sum(event.event_type == "legacy_transition_imported" for event in hfsm_events) == 1
    assert sum(event.event_type == "run_transition" for event in hfsm_events) == 4
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



def test_legacy_research_history_is_backfilled_once_without_mutating_legacy_events(client, db):
    from datetime import datetime, timedelta

    from app.models.research import ResearchStudy
    from app.models.scientific_workflow import ScientificWorkflowEvent, ScientificWorkflowRun, ScientificWorkflowStep
    from app.models.user import User
    from app.models.workflow import ResearchWorkflowEvent

    owner_token = _token(client, "legacy_hfsm_owner", "legacy-hfsm-owner@nexora.test")
    owner = db.query(User).filter(User.email == "legacy-hfsm-owner@nexora.test").one()
    study = ResearchStudy(
        user_id=owner.id,
        name="Ancienne étude Research",
        objective="Préserver son historique de workflow.",
        hypothesis="Le backfill ne réécrit pas les événements hérités.",
        status="analysis",
    )
    db.add(study)
    db.flush()
    created_at = datetime.utcnow() - timedelta(days=2)
    legacy_rows = [
        ResearchWorkflowEvent(
            study_id=study.id, actor_id=owner.id, from_state=None, to_state="draft",
            action="created", payload={"legacy": "created"}, created_at=created_at,
        ),
        ResearchWorkflowEvent(
            study_id=study.id, actor_id=owner.id, from_state="draft", to_state="data_review",
            action="submit_for_review", payload={"legacy": "submitted"},
            created_at=created_at + timedelta(minutes=1),
        ),
        ResearchWorkflowEvent(
            study_id=study.id, actor_id=owner.id, from_state="data_review", to_state="analysis",
            action="approve_data", reason="Approved before HFSM bridge.",
            payload={"legacy": "approved"}, created_at=created_at + timedelta(minutes=2),
        ),
    ]
    db.add_all(legacy_rows)
    db.commit()
    original_ids = [row.id for row in legacy_rows]

    headers = {"Authorization": f"Bearer {owner_token}"}
    status = client.get(f"/api/v1/research/studies/{study.id}/workflow", headers=headers)
    assert status.status_code == 200, status.text
    canonical = status.json()["scientific_workflow"]
    assert canonical["state"] == "analysis"
    assert canonical["state_version"] == 2

    run = db.query(ScientificWorkflowRun).filter_by(id=canonical["run_id"]).one()
    imported = db.query(ScientificWorkflowEvent).filter_by(
        workflow_run_id=run.id, event_type="legacy_transition_imported"
    ).order_by(ScientificWorkflowEvent.created_at.asc()).all()
    assert [item.payload["legacy_event_id"] for item in imported] == original_ids
    assert [item.created_at for item in imported] == [row.created_at for row in legacy_rows]
    assert db.query(ScientificWorkflowStep).filter_by(workflow_run_id=run.id).count() == 0

    second = client.get(f"/api/v1/research/studies/{study.id}/workflow", headers=headers)
    assert second.status_code == 200
    assert second.json()["scientific_workflow"]["run_id"] == run.id
    assert db.query(ScientificWorkflowRun).filter_by(
        workflow_key="nexora.research-study-lifecycle",
        scope_type="research_study",
        scope_id=str(study.id),
    ).count() == 1
    assert db.query(ScientificWorkflowEvent).filter_by(
        workflow_run_id=run.id, event_type="legacy_transition_imported"
    ).count() == 3

    legacy_after = db.query(ResearchWorkflowEvent).filter(
        ResearchWorkflowEvent.id.in_(original_ids)
    ).order_by(ResearchWorkflowEvent.id.asc()).all()
    assert [row.id for row in legacy_after] == original_ids
    assert [row.to_state for row in legacy_after] == ["draft", "data_review", "analysis"]
