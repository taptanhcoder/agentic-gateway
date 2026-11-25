# agentic-gateway
An LLM API gateway that discovers providers from GitHub and public catalogs, validates endpoints, and gives agents a single OpenAI Responses-compatible API with per-agent keys, quota tracking, and automatic failover.

## Run the foundation on WSL2

Enable Docker Desktop integration for your Ubuntu distribution. From the repository
root, generate a local database password once and start the stack:

```bash
test -e .env || (umask 077 && echo "POSTGRES_PASSWORD=$(openssl rand -hex 24)" > .env)
docker compose up --build -d
curl -i http://localhost:8000/health/live
curl -i http://localhost:8000/health/ready
docker compose logs migrate agentic-gateway-worker
```

Readiness returns 200 only at the expected database revision. Stop the stack with
`docker compose down`. Keep the named volume and the same password for subsequent
starts. The worker waits for shutdown signals until the source registry is built.
To run unit tests with Python 3.12, use `uv run --locked pytest`.
