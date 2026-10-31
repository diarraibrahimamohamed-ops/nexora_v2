import pytest

from app.services.scientific_provenance import (
    ProvenanceError,
    build_analysis_manifest,
    build_workflow_input_manifest,
    build_workflow_output_manifest,
    canonical_json_bytes,
    sha256_json,
    software_fingerprint,
)


def test_canonical_json_digest_is_independent_of_dictionary_insertion_order():
    first = {"z": 3, "a": {"y": 2, "x": 1}}
    second = {"a": {"x": 1, "y": 2}, "z": 3}
    assert canonical_json_bytes(first) == canonical_json_bytes(second)
    assert sha256_json(first) == sha256_json(second)


def test_canonical_json_rejects_non_finite_or_non_json_values():
    with pytest.raises(ProvenanceError, match="canonical JSON"):
        sha256_json({"invalid": float("nan")})
    with pytest.raises(ProvenanceError, match="canonical JSON"):
        sha256_json({"invalid": object()})


def test_analysis_manifest_binds_algorithm_inputs_parameters_and_output():
    manifest = build_analysis_manifest(
        algorithm="four_parameter_logistic",
        algorithm_version="N3XORA_4PL_CLUSTER_BOOTSTRAP_V1",
        inputs=[{
            "role": "selected_biological_measurements",
            "object_type": "research_assay_measurements",
            "object_id": "assay:42",
            "sha256": "a" * 64,
            "record_count": 21,
        }],
        parameters={"direction": "increasing", "bootstrap_iterations": 1000},
        output_sha256="b" * 64,
        execution={"mode": "workflow", "task_name": "experimental.run_4pl"},
        software={
            "application": "Nexora v2",
            "engine_module": "assay_statistics",
            "python_version": "3.12.0",
            "dependencies": {"numpy": "2.0.0", "scipy": "1.13.1"},
        },
        interpretation_limit="Exploratory only.",
    )

    assert manifest["schema"] == "nexora.scientific-provenance/v1"
    assert manifest["schema_version"] == "1.0.0"
    assert manifest["manifest_type"] == "scientific_analysis"
    assert manifest["algorithm"]["version"] == "N3XORA_4PL_CLUSTER_BOOTSTRAP_V1"
    assert manifest["parameters_sha256"] == sha256_json(manifest["parameters"])
    assert manifest["output"]["sha256"] == "b" * 64
    unsigned = {key: value for key, value in manifest.items() if key != "manifest_sha256"}
    assert manifest["manifest_sha256"] == sha256_json(unsigned)


def test_workflow_input_manifest_preserves_existing_fields_and_is_sealed():
    manifest = build_workflow_input_manifest({
        "sequence_length": 24,
        "sequence_sha256": "c" * 64,
    })
    assert manifest["sequence_length"] == 24
    assert manifest["sequence_sha256"] == "c" * 64
    assert manifest["manifest_type"] == "scientific_workflow_input"
    assert len(manifest["manifest_sha256"]) == 64


def test_workflow_output_manifest_binds_completed_steps_and_review_state():
    manifest = build_workflow_output_manifest(
        workflow_run_id=7,
        workflow_key="nexora.test",
        definition_version="1.0.0",
        steps=[{
            "step_id": 12,
            "step_key": "analyse",
            "task_name": "analysis.run",
            "queue_name": "analysis",
            "output": {"result_sha256": "d" * 64},
        }],
        requires_quality_review=True,
    )
    assert manifest["manifest_type"] == "scientific_workflow_output"
    assert manifest["workflow"]["run_id"] == 7
    assert manifest["steps"][0]["output_sha256"] == sha256_json({"result_sha256": "d" * 64})
    assert manifest["steps"][0]["output_keys"] == ["result_sha256"]
    assert manifest["completion"]["state"] == "steps_succeeded"
    assert len(manifest["manifest_sha256"]) == 64

def test_runtime_fingerprint_records_engine_and_dependency_versions():
    software = software_fingerprint("assay_statistics")
    assert software["application"] == "Nexora v2"
    assert software["engine_module"] == "assay_statistics"
    assert software["python_version"]
    assert set(software["dependencies"]) == {"numpy", "scipy"}
    assert all(software["dependencies"].values())

