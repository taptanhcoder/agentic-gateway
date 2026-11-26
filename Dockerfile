FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.10 /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

COPY pyproject.toml uv.lock README.md alembic.ini ./
COPY src/ ./src/

FROM base AS test
RUN uv sync --locked --group dev --no-editable --no-cache
COPY tests/ ./tests/
RUN mkdir -p /app/tests/fixtures/tls \
    && openssl req -x509 -newkey rsa:2048 -nodes \
       -keyout /app/tests/fixtures/tls/test-key.pem \
       -out /app/tests/fixtures/tls/test-cert.pem -days 3650 \
       -subj /CN=localhost -addext 'subjectAltName=IP:127.0.0.1,DNS:localhost' \
       >/dev/null 2>&1 \
    && chmod 644 /app/tests/fixtures/tls/*.pem
USER app
CMD ["python", "-m", "pytest", "-p", "no:cacheprovider"]

FROM base AS runtime
RUN uv sync --locked --no-dev --no-editable --no-cache

USER app

CMD ["uvicorn", "agentic_gateway.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
