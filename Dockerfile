# ============================================
# OpenSquad Docker Image
# Multi-stage build: frontend build + Python runtime
# ============================================

# ── Stage 1: Build frontend ──────────────────────────
FROM node:20-slim AS frontend-build

WORKDIR /build
# The committed package-lock.json is generated on Windows and only records
# win32 optional deps (npm keeps only the current platform's binaries in the
# lock's packages section), so npm ci/install cannot install
# @rollup/rollup-linux-x64-gnu inside the Linux image. Install from
# package.json instead (versions pinned by semver); the lockfile stays for
# reproducible local/CI installs on Windows.
COPY src/opensquad/gateway/nexuschat-pro/package.json ./
RUN npm install --ignore-scripts

COPY src/opensquad/gateway/nexuschat-pro/ ./
RUN npm run build

# ── Stage 2: Python runtime ─────────────────────────
FROM python:3.11-slim AS runtime

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies (git for plugin install, curl for healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git curl && \
    rm -rf /var/lib/apt/lists/*

# Install Python dependencies + package.
# README.md is required by pyproject.toml (readme = "README.md") for metadata;
# src/ is required for the editable install (src-layout, packages.find where=["src"]).
# C-4 note: a true dependency-layer cache would require `uv sync --frozen`
# with pyproject.toml + uv.lock copied before src/; uv is not yet installed in
# this image, so we keep the straightforward order. The image installs
# non-editable (stable artifact) instead of editable.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Copy helper scripts
COPY scripts/ ./scripts/

# Copy built frontend from stage 1
COPY --from=frontend-build /build/dist/ ./src/opensquad/gateway/nexuschat-pro/dist/

# Copy example config if no config is mounted
COPY src/system_config.example.json ./src/system_config.example.json

# Create directories for runtime data
RUN mkdir -p /data/workspaces /data/logs /data/plugins /data/sessions

# Switch to non-root user for security
RUN groupadd -r opensquad && useradd -r -g opensquad -d /app -s /sbin/nologin opensquad
RUN chown -R opensquad:opensquad /app /data

# Entrypoint script — copy + chmod must run as root (before USER opensquad)
# because non-root can't chmod files it doesn't own. The chown above then
# gives the opensquad user executable access to the entrypoint.
COPY docker-entrypoint.sh ./docker-entrypoint.sh
RUN chmod +x ./docker-entrypoint.sh && chown opensquad:opensquad ./docker-entrypoint.sh
USER opensquad

# Expose ports:
#   9555 - Gateway Backend (FastAPI)
#   9600 - Launcher (Agent management)
#   9720 - Plugin Registry
EXPOSE 9555 9600 9720

# Volumes for persistent data
VOLUME ["/data"]

ENTRYPOINT ["./docker-entrypoint.sh"]
