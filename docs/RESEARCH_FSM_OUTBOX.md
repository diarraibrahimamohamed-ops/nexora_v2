# N3XORA — Research FSM and Transactional Outbox

## Scope

This increment introduces a state machine for the lifecycle of a research study and a persistent outbox for selected asynchronous research jobs. It is additive: existing endpoints, data models, Celery task names and scientific engines remain in place.

## Study states

| State | Meaning |
|---|---|
| `draft` | Study created; objective and hypothesis can still be edited |
| `data_review` | Inputs and scientific framing are submitted for review |
| `analysis` | Required inputs have been reviewed; computational work may proceed |
| `results_review` | At least one computational output is available for interpretation |
| `blocked` | Work is paused pending a documented reason or external dependency |
| `completed` | Computational study closed; this does not imply experimental or clinical validation |
| `cancelled` | Study cancelled |

### Transition policy

| Action | From | To | Server-side preconditions |
|---|---|---|---|
| `submit_for_review` | draft | data_review | Objective and hypothesis are present |
| `approve_data` | data_review | analysis | At least one target or variant exists; target sequences are non-empty |
| `submit_results` | analysis | results_review | A completed docking run or completed structural/docking comparison exists |
| `complete_computational` | results_review | completed | A verifiable computational output exists; reviewer justification is required |
| `block` | active state | blocked | Justification is required |
| `resume` | blocked | previous active state | Justification is required |
| `cancel` | non-terminal state | cancelled | Justification is required |

The state machine is a workflow control, not a scientific classifier. It does not assert that a result is biologically true. A completed computational study must not be described as experimentally validated.

Computational operations are server-gated to the `analysis` state: structure resolution/upload, structural comparisons, docking, paired WT/variant docking and screening preparation/launch. A study in `draft` or `data_review` cannot start these jobs. This gate is enforced in the API, not only in the browser.

## API

- `GET /api/v1/research/studies/{study_id}/workflow`
- `POST /api/v1/research/studies/{study_id}/workflow/transitions`

Example request:

```json
{
  "action": "submit_for_review"
}
```

Use the optional `Idempotency-Key` request header (1–128 characters) when a client may retry a transition. The server stores the resulting event and returns the original event on replay.

Every successful transition records actor, previous state, next state, action, justification, readiness summary, policy version and UTC timestamp in `research_workflow_events`.

## Transactional outbox

Selected study-related Celery submissions are now persisted in `research_task_outbox` in the same database transaction as their associated jobs:

- research structure resolution;
- standalone Vina docking;
- paired WT/variant Vina docking;
- Vina runs created by virtual screening.

The dispatcher claims due rows, publishes them to the declared Celery queue and marks them published. Failed publications return to pending with bounded exponential backoff. A stale publishing lease can be retried after a dispatcher crash.

This is an **at-least-once** delivery design. A process can fail after broker acceptance and before the outbox acknowledgement, so duplicate delivery is possible. Stable Celery task IDs aid tracing but do not by themselves provide exactly-once execution. Workers and future retry/reconciliation tooling must remain idempotent.

## Deployment

The application bootstrap creates the new tables for existing development databases. The explicit SQL migration is:

`infra/postgres/migrations/20261031_research_fsm_outbox.sql`

The dedicated `outbox-dispatcher` service is included in `docker-compose.yml`. Start or recreate it with:

```bash
docker compose up -d --build api worker outbox-dispatcher
docker compose logs --tail=100 outbox-dispatcher
```

Do not run `docker compose down -v` on a database containing valuable research data.

## Deliberate limitations of this increment

- The FSM currently governs the study lifecycle; it does not yet implement separate per-variant, per-structure or per-experiment state machines.
- The readiness rules are intentionally conservative first-pass rules and must be refined by organism-specific research profiles.
- External provider success (for example Tamarind) and local installation readiness must still be validated in the deployment environment.
- The outbox dispatcher is not a substitute for operational monitoring, dead-letter handling, or a controlled replay UI.
- Experimental evidence and assay review remain a separate next increment.
