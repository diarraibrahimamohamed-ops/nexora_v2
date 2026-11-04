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



def test_research_study_completes_full_lifecycle_with_independent_reviewers_and_persisted_output(
    client, db, tmp_path, monkeypatch
):
    import json

    from app.config import settings
    from app.models.docking import DockingJob, JobStatus
    from app.models.research import ResearchDockingRun, ResearchTarget
    from app.models.scientific_workflow import (
        ScientificWorkflowEvent,
        ScientificWorkflowRun,
        ScientificWorkflowStep,
    )
    from app.models.user import User
    from app.models.workflow import ResearchTaskOutbox, ResearchWorkflowEvent

    owner_token = _token(
        client, "full_lifecycle_owner", "full-lifecycle-owner@nexora.test"
    )
    owner_headers = {"Authorization": f"Bearer {owner_token}"}

    owner = db.query(User).filter(
        User.email == "full-lifecycle-owner@nexora.test"
    ).one()

    created = client.post(
        "/api/v1/research/studies",
        headers=owner_headers,
        json={
            "name": "Étude intégrée du cycle Research",
            "disease": "exploratory",
            "pathogen": "Test organism",
            "country_focus": "Mali",
            "region_scope": "Sahel",
            "objective": "Vérifier le cycle complet d'une étude computationnelle.",
            "hypothesis": "Un résultat de docking persisté permet de soumettre le dossier à revue.",
        },
    )
    assert created.status_code == 200, created.text
    study_id = created.json()["study"]["id"]

    lifecycle = db.query(ScientificWorkflowRun).filter_by(
        workflow_key="nexora.research-study-lifecycle",
        scope_type="research_study",
        scope_id=str(study_id),
    ).one()
    assert lifecycle.state == "draft"
    assert lifecycle.state_version == 0
    assert lifecycle.definition_snapshot["execution_mode"] == "state_machine"
    assert db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=lifecycle.id
    ).count() == 0

    target_response = client.post(
        f"/api/v1/research/studies/{study_id}/targets",
        headers=owner_headers,
        json={
            "state": "wt",
            "protein_name": "Workflow test target",
            "protein_sequence": "MKTAYIAKQRQISFVKSHFSRQ",
            "structure_source": "user_provided",
        },
    )
    assert target_response.status_code == 200, target_response.text
    target_id = target_response.json()["target_id"]

    submitted = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers={**owner_headers, "Idempotency-Key": "full-lifecycle-submit"},
        json={"action": "submit_for_review"},
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["state"] == "data_review"

    data_reviewer_token = _token(
        client, "full_lifecycle_data_reviewer", "full-lifecycle-data-reviewer@nexora.test"
    )
    data_reviewer = db.query(User).filter(
        User.email == "full-lifecycle-data-reviewer@nexora.test"
    ).one()
    data_reviewer.is_admin = True
    db.commit()
    data_review = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/review-data",
        headers={"Authorization": f"Bearer {data_reviewer_token}"},
        json={"reason": "Identité de la cible et données d'entrée contrôlées indépendamment."},
    )
    assert data_review.status_code == 200, data_review.text
    assert data_review.json()["state"] == "analysis"
    assert data_review.json()["scientific_workflow"]["state_version"] == 2

    # Exercise the actual Research docking submission contract and transactional
    # outbox. The numerical result below is a deterministic test fixture standing
    # in for the worker output; it is not an experimental or biological finding.
    monkeypatch.setattr(settings, "RESULTS_DIR", str(tmp_path))
    structure_path = tmp_path / "reviewed_target.pdb"
    structure_path.write_text("HEADER    TEST STRUCTURE\nEND\n", encoding="utf-8")

    target = db.query(ResearchTarget).filter_by(id=target_id, study_id=study_id).one()
    target.structure_path = str(structure_path)
    target.structure_status = "accepted_for_docking_review"
    target.structure_source = "user_provided"
    target.structure_validation = {
        "status": "accepted_for_docking_review",
        "test_fixture": True,
    }
    db.commit()

    docking_response = client.post(
        f"/api/v1/research/studies/{study_id}/docking",
        headers=owner_headers,
        json={
            "target_id": target_id,
            "ligand_smiles": "CCO",
            "reference_state": "wt",
            "max_pockets": 2,
        },
    )
    assert docking_response.status_code == 202, docking_response.text
    docking_payload = docking_response.json()
    assert docking_payload["dispatch"] == "transactional_outbox"

    docking_job = db.query(DockingJob).filter_by(
        id=docking_payload["docking_job_id"]
    ).one()
    research_run = db.query(ResearchDockingRun).filter_by(
        id=docking_payload["research_run_id"]
    ).one()
    outbox = db.query(ResearchTaskOutbox).filter_by(
        event_key=docking_payload["task_id"]
    ).one()
    assert outbox.task_name == "docking.run_vina"
    assert outbox.queue_name == "docking"
    assert outbox.args[0] == docking_job.id
    assert research_run.docking_job_id == docking_job.id

    # No computational output means the study cannot enter result review.
    missing_output = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers=owner_headers,
        json={"action": "submit_results"},
    )
    assert missing_output.status_code == 409, missing_output.text

    result_path = tmp_path / f"research_docking_{docking_job.id}.json"
    result_payload = {
        "success": True,
        "num_poses": 1,
        "vina_best_score": -6.2,
        "boltzmann_effective_score": -5.9,
        "boltzmann_mean_score": -5.8,
        "docking_engine": "AutoDock Vina",
        "aggregation_method": "ASP test fixture",
    }
    result_path.write_text(json.dumps(result_payload), encoding="utf-8")
    docking_job.status = JobStatus.completed
    docking_job.progress = 100
    docking_job.result_path = str(result_path)
    docking_job.status_message = "Completed by deterministic workflow test fixture."
    research_run.status = "completed"
    research_run.result_summary = {
        "vina_best_score": -6.2,
        "boltzmann_effective_score": -5.9,
        "num_poses": 1,
        "result_artifact": result_path.name,
    }
    db.commit()

    detail = client.get(
        f"/api/v1/research/studies/{study_id}",
        headers=owner_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["docking_runs"][0]["status"] == "completed"
    assert detail.json()["docking_runs"][0]["result_summary"]["vina_best_score"] == -6.2
    assert detail.json()["docking_runs"][0]["result_summary"]["boltzmann_effective_score"] == -5.9

    submitted_results = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/transitions",
        headers={**owner_headers, "Idempotency-Key": "full-lifecycle-submit-results"},
        json={"action": "submit_results"},
    )
    assert submitted_results.status_code == 200, submitted_results.text
    assert submitted_results.json()["state"] == "results_review"
    assert submitted_results.json()["scientific_workflow"]["state_version"] == 3

    results_reviewer_token = _token(
        client, "full_lifecycle_results_reviewer", "full-lifecycle-results-reviewer@nexora.test"
    )
    results_reviewer = db.query(User).filter(
        User.email == "full-lifecycle-results-reviewer@nexora.test"
    ).one()
    results_reviewer.is_admin = True
    db.commit()
    final_review = client.post(
        f"/api/v1/research/studies/{study_id}/workflow/review-results",
        headers={"Authorization": f"Bearer {results_reviewer_token}"},
        json={"reason": "Résultat computationnel et dossier de provenance contrôlés indépendamment."},
    )
    assert final_review.status_code == 200, final_review.text
    assert final_review.json()["state"] == "completed"
    assert final_review.json()["scientific_workflow"]["state_version"] == 4

    db.refresh(lifecycle)
    db.refresh(docking_job)
    db.refresh(research_run)
    assert lifecycle.state == "completed"
    assert lifecycle.state_version == 4
    assert docking_job.status == JobStatus.completed
    assert research_run.status == "completed"
    assert db.query(ScientificWorkflowStep).filter_by(
        workflow_run_id=lifecycle.id
    ).count() == 0

    legacy_events = db.query(ResearchWorkflowEvent).filter_by(
        study_id=study_id
    ).order_by(ResearchWorkflowEvent.id.asc()).all()
    assert [event.action for event in legacy_events] == [
        "created",
        "submit_for_review",
        "approve_data",
        "submit_results",
        "complete_computational",
    ]
    assert legacy_events[2].actor_id == data_reviewer.id
    assert legacy_events[4].actor_id == results_reviewer.id
    assert data_reviewer.id != owner.id

    canonical_events = db.query(ScientificWorkflowEvent).filter_by(
        workflow_run_id=lifecycle.id
    ).order_by(ScientificWorkflowEvent.id.asc()).all()
    imported = [event for event in canonical_events if event.event_type == "legacy_transition_imported"]
    transitions = [event for event in canonical_events if event.event_type == "run_transition"]
    assert len(imported) == 1
    assert imported[0].payload["legacy_event_id"] == legacy_events[0].id
    assert len(transitions) == 4
    assert [event.to_state for event in transitions] == [
        "data_review",
        "analysis",
        "results_review",
        "completed",
    ]
    assert transitions[1].actor_user_id == data_reviewer.id
    assert transitions[3].actor_user_id == results_reviewer.id
    assert lifecycle.input_manifest["manifest_type"] == "scientific_workflow_input"
    assert lifecycle.input_manifest["manifest_sha256"]

    timeline = client.get(
        f"/api/v1/research/studies/{study_id}/workflow",
        headers=owner_headers,
    )
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["state"] == "completed"
    assert timeline.json()["scientific_workflow"]["run_id"] == lifecycle.id
    assert timeline.json()["counts"]["computational_outputs"] == 1

    # Lifecycle governance is synchronous; only the explicitly submitted docking
    # task appears in the transactional outbox, never a workflow-execution task.
    assert db.query(ResearchTaskOutbox).filter_by(
        task_name="scientific_workflow.execute_step"
    ).count() == 0
