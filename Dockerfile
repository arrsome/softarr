# syntax=docker/dockerfile:1.9
#
# Multi-stage build using uv (astral-sh) with BuildKit cache mounts.
#
# Highlights:
#   - uv is pulled in at build time via --mount=from=ghcr.io/astral-sh/uv
#     and never installed into the final image.
#   - /root/.cache/uv is a persistent BuildKit cache mount, so rebuilds
#     reuse previously downloaded wheels.
#   - Dependency install and project install are split into two layers so
#     that source-only changes don't bust the (slow) dependency layer.
#   - --locked ensures reproducible builds from uv.lock.
#
# Build: DOCKER_BUILDKIT=1 docker build -t arrsomedev/softarr:dev .

# ---------------------------------------------------------------------------
# Stage 1: build the virtual environment
# ---------------------------------------------------------------------------
FROM python:3.14.4-slim-trixie AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Step 1: install dependencies only. This layer is cached until
# pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock ./
RUN --mount=from=ghcr.io/astral-sh/uv:0.11.6,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Step 2: copy source and install the project itself into the venv.
# By default uv installs the project editably, which keeps
# src/softarr/core/i18n.py's relative ``locales/`` lookup working at runtime.
COPY README.md ./
COPY src ./src
RUN --mount=from=ghcr.io/astral-sh/uv:0.11.6,source=/uv,target=/usr/local/bin/uv \
    --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------------------------------------------------------------------------
# Stage 2: minimal runtime image
# ---------------------------------------------------------------------------
FROM python:3.14.4-slim-trixie AS runtime

# Baked service user. The container runs as uid/gid 1000 by default. To run
# as a different host uid (e.g. for bind mounts), use Docker's native --user
# flag or compose's `user:` directive -- no entrypoint magic required. See
# docs/configuration.md "Running as a non-root user".
RUN groupadd --gid 1000 softarr \
    && useradd --create-home --uid 1000 --gid softarr softarr

WORKDIR /app

# Copy the pre-built venv from the builder stage.
COPY --from=builder --chown=softarr:softarr /app/.venv /app/.venv

# Copy the source tree and everything the app reads at runtime.
# src/ is needed because uv installed the project editably, so the .pth file
# in site-packages points at /app/src/softarr. migrations/, alembic.ini,
# and locales/ are all referenced at runtime from paths relative to /app.
COPY --chown=softarr:softarr src ./src
COPY --chown=softarr:softarr migrations ./migrations
COPY --chown=softarr:softarr alembic.ini ./alembic.ini
COPY --chown=softarr:softarr locales ./locales

# Entrypoint script -- runs softarr-init before gunicorn so the first-boot
# setup (table creation, default admin) is done before any worker starts.
COPY docker/entrypoint.sh /usr/local/bin/softarr-entrypoint
RUN chmod +x /usr/local/bin/softarr-entrypoint

# HOME is set defensively to /tmp so that any incidental ~ lookup (e.g. from
# a library call to os.path.expanduser) resolves to a writable path even
# when the container is started with --user <uid> for a uid that has no
# entry in /etc/passwd. /tmp is used instead of /data to avoid stray dotfile
# writes landing in the operator's volume.
ENV PATH="/app/.venv/bin:$PATH" \
    VIRTUAL_ENV=/app/.venv \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOME=/tmp \
    CONFIG_DIR=/data

# Data directory for softarr.ini and sqlite db (override with CONFIG_DIR env var).
# Chowned to the baked softarr user; bind-mount users must pre-chown their
# host directory to match whatever uid they pass via --user.
RUN mkdir -p /data && chown softarr:softarr /data

EXPOSE 8000

USER softarr

# Entrypoint runs softarr-init then execs gunicorn. Extra args forwarded to
# gunicorn via "$@", so operators can override worker counts, timeouts, etc.
# via docker run / compose `command:` without losing the init step.
ENTRYPOINT ["/usr/local/bin/softarr-entrypoint"]
CMD []
