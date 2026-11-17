# Northstar support assistant: one image for local runs and Render.
#
# Build (from the repository root):
#   docker build -t northstar-app .
# Run with the offline dry provider:
#   docker run --rm -e MODEL_PROVIDER=dry -p 127.0.0.1:8000:8000 northstar-app

# Stage 0: borrow the uv binary from its official image (pinned version).
FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

# Base image: slim Debian with exactly Python 3.13.17 (pinned, not "latest").
FROM python:3.13.17-slim

# CA certificates let the app make HTTPS calls (Groq, Langfuse) later.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=uv /uv /uvx /bin/

# 1) Dependencies first: this layer is reused from cache until
#    pyproject.toml or uv.lock change, so code edits rebuild in seconds.
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project --no-cache

# 2) Then the application code and the data it reads.
COPY src ./src
COPY data ./data

# Run as an unprivileged user, never as root.
RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p reports/generated \
    && chown appuser:appuser reports/generated
USER appuser

ENV PATH="/app/.venv/bin:$PATH" \
    MODEL_PROVIDER=dry \
    TRACE_SINK=jsonl \
    PORT=8000
EXPOSE 8000

# Render sets $PORT; locally it defaults to 8000.
CMD ["sh", "-c", "exec uvicorn src.northstar.app:app --host 0.0.0.0 --port ${PORT}"]
