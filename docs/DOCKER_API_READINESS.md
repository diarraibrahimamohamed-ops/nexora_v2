# Docker Compose API readiness

N3XORA serves the frontend through Nginx and forwards `/api/` requests to the
FastAPI service at `api:8000`. Container creation is not the same as API
readiness: Uvicorn's reload supervisor can start before its serving process
has completed the FastAPI lifespan.

The API service therefore exposes a Docker healthcheck against its real
`/health` endpoint. FastAPI only begins serving requests after its startup
lifespan has completed, including database schema initialization and
administrator bootstrap. Nginx uses the Compose
`depends_on: condition: service_healthy` condition and is not started until
that check succeeds.

## Local verification

From the repository root, with a valid `.env` already configured:

```bash
docker compose up -d --build api nginx
docker compose ps
docker compose logs --since=5m api nginx
curl -i --max-time 5 http://127.0.0.1/health
```

The expected health response is HTTP 200 with `{"status":"ok"}`. The API
container should show `healthy` in `docker compose ps`.

For an authentication-path check, register a disposable test account and
authenticate through Nginx:

```bash
curl -i -X POST http://127.0.0.1/api/v1/auth/register \
  -H 'Content-Type: application/json' \
  --data '{"username":"readiness-test","email":"readiness-test@example.invalid","password":"temporary-test-password"}'

curl -i -X POST http://127.0.0.1/api/v1/auth/login \
  -H 'Content-Type: application/json' \
  --data '{"email":"readiness-test@example.invalid","password":"temporary-test-password"}'
```

Use a unique email address for each run. Do not reuse real credentials in
smoke tests.

## Scope

This readiness gate prevents the initial Nginx/API startup race. It does not
make Docker restart dependent services automatically when an already-running
API later becomes unhealthy. The Nginx Docker DNS resolver remains responsible
for re-resolving the API service address after container replacement; runtime
recovery and external production load-balancer behavior should be monitored
separately.
