# Nginx smoke-test configurations

These configurations are for local/manual integration tests, not production.

- `smoke-full.conf`: full Nginx config (includes events/http blocks) on port 18080.
- `smoke-server.conf`: server block intended for an existing http context.
- `test-api-proxy.conf`: full local test config on port 80.

The main Docker Compose stack uses `infra/nginx/nginx.conf`.
