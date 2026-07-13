# syntax=docker/dockerfile:1
# Home Assistant MCP Server Add-on
# Multi-stage build: uv for dependency resolution, slim Python for runtime
# Python 3.13 - Security support until 2029-10
# No bashio needed - pure Python startup script

# --- Build stage: install dependencies with uv ---
FROM ghcr.io/astral-sh/uv:0.11.27-python3.13-alpine@sha256:633713817551348828c0feba52e2c9295ce2af7e9bc82bcc84b40cd9ab4d2194 AS builder

WORKDIR /app

# Compile bytecode for faster startup; copy mode required with cache mounts
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies first (cached separately from source changes)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

# Copy source and install the project itself
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- Runtime stage: clean image without uv ---
FROM python:3.13-alpine@sha256:399babc8b49529dabfd9c922f2b5eea81d611e4512e3ed250d75bd2e7683f4b0

WORKDIR /app

# Copy the virtual environment and startup script from builder
COPY --from=builder /app/.venv /app/.venv
COPY homeassistant-addon/start.py /

# Activate virtual environment via PATH
ENV PATH="/app/.venv/bin:$PATH"

# Propagate the build version into the runtime so startup logs report the
# correct version. The addon workflow always passes BUILD_VERSION; standalone
# Docker pulls that don't pass it fall through to package metadata.
ARG BUILD_VERSION
ARG BUILD_ARCH
ARG BUILD_COMMIT
ENV HA_MCP_BUILD_VERSION=${BUILD_VERSION} \
    HA_MCP_BUILD_COMMIT=${BUILD_COMMIT}

LABEL \
    io.hass.name="Home Assistant MCP Server - DAG Studio" \
    io.hass.description="Private causal DAG tools for Home Assistant via MCP" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="addon" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.source="https://github.com/resace3/ha-mcp" \
    org.opencontainers.image.revision="${BUILD_COMMIT}"

CMD ["python3", "/start.py"]
