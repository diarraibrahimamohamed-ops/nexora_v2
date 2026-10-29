# Contributing to Nexora v2

Contributions must preserve software security, reproducibility and scientific honesty.

## Before opening a pull request

1. Explain the problem and expected behavior.
2. Keep changes focused and avoid mixing unrelated refactors with security fixes.
3. Add regression tests for bug fixes, security controls and workflow changes.
4. Record external dependencies, provider requirements, reference-data versions and licenses.
5. Never commit API keys, tokens, personal identifiers or private sequence data.

## Backend checks

From the repository root:

```bash
python -m pip install -r backend/requirements.txt
python -m compileall -q backend/app backend/tests
pytest -q backend/tests/test_phytochemistry.py backend/tests/test_research_bioevidence.py backend/tests/test_amrfinder_readiness.py backend/tests/test_config_security.py backend/tests/test_heuristic_scientific_guards.py backend/tests/test_virtual_screening_phase7.py backend/tests/test_amrfinder_integration.py backend/tests/test_rna_structure_integration.py backend/tests/test_structure_validation_phase3.py
```

## Frontend and containers

```bash
node --check frontend/js/api.js
node --check frontend/js/nexora-resistance.js
node --check frontend/js/rna-secondary.js
docker compose config --quiet
docker build --build-arg INSTALL_AMRFINDERPLUS=true -t nexora-v2:local backend
```

The Docker image installs the AMRFinderPlus binary. Its reference database is initialized separately, as described in docs/AMRFINDERPLUS_SETUP.md.

## Scientific review requirements

- Distinguish experimental observations, documented associations and computational hypotheses.
- Never label a predicted structure as experimental.
- Do not present AMRFinderPlus hits as an antibiogram or clinical resistance probability.
- Preserve reference accessions, software and database versions, parameters, and relevant input/output hashes.
- Document limitations and negative results, not just favorable outputs.
- Any change that alters scientific interpretation must include a validation note and receive independent review before being described as validated.

## Pull request expectations

Describe risk, tests, migration/deployment effects and user-visible behavior. CI must pass before merge. Ordinary CI tests should mock external provider calls rather than require real API keys.
