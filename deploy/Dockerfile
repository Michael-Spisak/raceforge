# RaceForge backend API image (spec 0006). Build from the repo root:
#   docker build -f deploy/Dockerfile -t raceforge-backend .
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev && useradd --uid 10001 --no-create-home raceforge
USER raceforge
EXPOSE 8000
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/v1/status', timeout=2)"]
CMD ["raceforge", "backend", "serve", "--port", "8000"]
