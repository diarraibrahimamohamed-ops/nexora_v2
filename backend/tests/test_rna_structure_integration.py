from app.main import app
from app.core.protein.tamarind_runner import build_tamarind_secondary_structure, discover_rna_fm_settings
from app.models.rna_structure_job import RnaStructureJob
from app.workers.rna_structure_tasks import predict_rna_secondary_structure_task


def test_rna_structure_routes_registered():
    paths={route.path for route in app.routes}
    assert "/api/v1/rna-structure/submit" in paths
    assert "/api/v1/rna-structure/{job_id}" in paths
    assert "/api/v1/rna-structure/{job_id}/artifact" in paths


def test_rna_structure_model_columns():
    assert RnaStructureJob.__tablename__=="rna_structure_jobs"
    assert {"user_id","sequence","status","metadata_json","artifact_path"}.issubset(set(RnaStructureJob.__table__.columns.keys()))


def test_rna_celery_task_registered():
    assert predict_rna_secondary_structure_task.name=="rna_structure.predict_secondary"


def test_rna_fm_schema_discovery_is_dynamic():
    payload={"tools":[{"name":"rna_fm_tool_v2","displayName":"RNA-FM secondary structure predictor",
        "settings":[{"name":"sequence","required":True},{"name":"task","required":True,
        "options":[{"label":"Secondary Structure Prediction","value":"ss_prediction"},{"label":"Other task","value":"other"}]}]}]}
    name,settings,error=discover_rna_fm_settings(payload,"ACGU")
    assert error is None
    assert name=="rna_fm_tool_v2"
    assert settings=={"sequence":"ACGU","task":"ss_prediction"}


def test_rna_fm_invalid_sequence_rejected_before_network(tmp_path):
    path,error,metadata=build_tamarind_secondary_structure("ACGTZ",str(tmp_path),tamarind_api_key="test-key")
    assert path is None
    assert error and "bases A/C/G/U/T" in error
    assert metadata=={}
