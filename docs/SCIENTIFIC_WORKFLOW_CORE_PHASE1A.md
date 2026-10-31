# Scientific Workflow Core — Phase 1A

**Status:** implementation candidate, not merged  
**Base:** main post-PR #10  
**Scope:** transactional submission consistency and basic completed-result replay protection.

## 1. Objective

Use the existing PostgreSQL transactional outbox consistently for asynchronous jobs. The business record and the intent to publish a Celery task must be committed together. This avoids the former two-transaction pattern in which a database job could survive while publication to Redis failed.

This increment does not introduce a new workflow database or alter scientific calculation engines.

## 2. Endpoints moved to the outbox

| Endpoint | Celery task name | Queue |
|---|---|---|
| `POST /api/v1/docking/submit` | `docking.run_vina` | `docking` |
| Async `POST /api/v1/analysis/sequence` | `analysis.run` | `analysis` |
| Async `POST /api/v1/analysis/afribio/align` | `analysis.run_afribio_alignment` | `analysis` |
| Async `POST /api/v1/analysis/afribio/phylo` | `analysis.run_afribio_phylo` | `analysis` |
| `POST /api/v1/hsa/submit` and legacy `/qsa/submit` | `hsa.run` | `qsa` |
| `POST /api/v1/rna-structure/submit` | `rna_structure.predict_secondary` | `structure` |

The existing Research study, screening, docking and structure-resolution paths already used the outbox and remain on it.

The response keeps the existing task identifier field and adds `dispatch: transactional_outbox` plus `outbox_event_key` where useful. The event key is also passed to Celery as the task ID by the existing dispatcher. Existing poll/status endpoints remain unchanged.

## 3. Transaction boundary

The route now performs the following steps in one SQLAlchemy transaction:

1. Create the domain job/analysis row.
2. Flush to obtain its database ID.
3. Create the `ResearchTaskOutbox` event with task name, queue, arguments and a UUID event key.
4. Store the event key on the domain job where a legacy task ID field exists.
5. Commit once.

The dispatcher remains responsible for publishing pending events. A publication failure returns the event to pending with exponential backoff. The stable event key is reused on subsequent delivery attempts.

The delivery guarantee remains **at-least-once**, not exactly-once. The event key is a stable deduplication identity; it does not by itself make a worker's external side effects exactly-once.

## 4. Duplicate-completion protection

The docking, asynchronous sequence-analysis, HSA, RNA-structure and research structure-resolution workers now check whether the persistent job is already completed before invoking the scientific calculation again. A completed record is returned as an idempotent replay.

This is a first protection layer, not a full distributed execution lease. Two deliveries racing while the same job is still running are not yet serialized by a durable claim/heartbeat protocol. Worker-loss recovery and concurrent duplicate suppression belong in the next increment.

AfriBio alignment and phylogeny currently use transient job identifiers and Celery result storage rather than dedicated persistent job/result tables. They are routed through the outbox here for publication reliability, but durable result history and full worker-level idempotency still need a persistent job model.

## 5. Regression tests added

- Generic async API submissions create an outbox event on the expected queue.
- Sequence, generic docking, HSA, RNA structure, AfriBio alignment and AfriBio phylogeny retain the expected response shape and task IDs.
- An outbox event rolls back with its parent business transaction.
- A redelivery after an ambiguous broker acknowledgement reuses the same Celery task ID.
- Existing outbox publication failure/backoff tests remain in place.

## 6. Deferred to Phase 1B

- Versioned workflow definitions and a generic HFSM state policy.
- DAG validation, deterministic topological ordering, fan-out/fan-in and dependency release.
- Persistent `WorkflowRun` and `WorkflowStep` records distinct from `ResearchStudy`.
- Durable execution leases, heartbeats, worker-loss recovery and cancellation semantics.
- Persistent AfriBio job/result records.
- A common tool registry and immutable execution/provenance manifest.

No new PostgreSQL migration is required by Phase 1A. The existing `research_task_outbox` schema is reused.

## 7. Deployment note

This branch is an implementation candidate. Do not deploy it before review and CI. The outbox dispatcher service must be running in the target environment; otherwise events remain safely pending in PostgreSQL but will not reach Celery.
