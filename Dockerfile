FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src PYTHONUNBUFFERED=1
COPY src src
COPY alembic.ini .
COPY migrations migrations
USER 65534:65534
CMD ["uvicorn", "agentic_gateway.api:create_app", "--factory", "--host", "0.0.0.0"]
