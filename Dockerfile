# ============================================================
# Stage 1: Builder — compile dependencies, keeps build tools
# ============================================================
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# ============================================================
# Stage 2: Runtime — lean final image, no build tools
# ============================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/install/bin:$PATH" \
    PYTHONPATH="/install/lib/python3.12/site-packages"

# Only runtime system deps (curl for health-check, mongodb-database-tools for
# the nightly backup job — PRO-111). The tools are NOT in Debian's default
# repos, so MongoDB's official apt repo is added first; gpg is needed only
# to import its signing key and is purged in the same layer.
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    gpg \
    && curl -fsSL -o /tmp/mongodb.asc https://www.mongodb.org/static/pgp/server-8.0.asc \
    && gpg --dearmor -o /usr/share/keyrings/mongodb-server-8.0.gpg /tmp/mongodb.asc \
    && rm /tmp/mongodb.asc \
    && echo "deb [signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/8.0 main" \
    > /etc/apt/sources.list.d/mongodb-org-8.0.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    mongodb-database-tools \
    && apt-get purge -y --auto-remove gpg \
    && rm -rf /var/lib/apt/lists/*

# PRO-111: the incident was "the binary wasn't in the image" — fail the build,
# not the 02:00 backup run, if the tools didn't land.
RUN mongodump --version && mongorestore --version

# Copy installed packages from builder
COPY --from=builder /install /usr/local

WORKDIR /app

# Copy project source
COPY . .

# Non-root user
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# Docker health check — polls /health every 30s, fails if app is not responding
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

EXPOSE 8000 8501

# Default: API server (override per service in docker-compose)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
