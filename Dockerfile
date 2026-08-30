# syntax=docker/dockerfile:1

# --- Stage 1: build the React frontend --------------------------------------
FROM node:22-slim AS frontend-builder
WORKDIR /app/frontend

# Separate copy of just the lockfiles first so `npm ci` is cached across
# rebuilds that only touch source, not dependencies.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python runtime -------------------------------------------------
# No system packages (poppler/ImageMagick/ghostscript) are needed for PDF
# rendering: pdfplumber's page.to_image() uses pypdfium2, which ships a
# prebuilt PDFium binary in its wheel - verified against the installed
# pdfplumber 0.11.10 (src/display.py imports pypdfium2 directly, no
# subprocess calls to any external tool anywhere in this codebase).
FROM python:3.12-slim AS runtime
WORKDIR /app

# Conservative on purpose: no local Docker to iterate against (see
# README "Deploying"), so a missing system library would only surface as
# a failed HF build several minutes later instead of being caught here.
# build-essential covers any dependency that falls back to a source build
# instead of finding a prebuilt wheel for this exact base image; libjpeg/
# zlib are what Pillow's wheel links against for JPEG/PNG support. None of
# this is strictly required by the verified-clean dependency chain
# (pdfplumber's page.to_image() uses pypdfium2's bundled PDFium binary,
# not poppler/ImageMagick/ghostscript via subprocess - see
# design/DESIGN.md), it's insurance against the one thing that can't be
# checked without a real build.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libjpeg62-turbo \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

# HF Spaces convention: run as a non-root user (uid 1000) rather than root.
RUN useradd -m -u 1000 appuser

COPY pyproject.toml ./
COPY src/ ./src/
COPY config/ ./config/

# Editable install: keeps `Path(__file__)`-based path resolution in
# config.py anchored to /app (this WORKDIR) exactly the way it resolves to
# the repo root in local dev - see config.py DEFAULT_CONFIG_PATH. A
# non-editable install would copy the package into site-packages instead,
# breaking that resolution.
RUN pip install --no-cache-dir -e ".[api]"

COPY --from=frontend-builder /app/frontend/dist ./frontend/dist

# All pipeline output (bronze/gold/cache/trace/uploads) lives under here -
# see config/default.yaml paths: and articles_pipeline.py/main.py, which
# create their own subdirectories on demand. Ephemeral by design on this
# deployment - see README "Deploying" for why that's an accepted tradeoff.
RUN mkdir -p /app/data && chown -R appuser:appuser /app

USER appuser

# Hugging Face Docker Spaces hardcode this port.
EXPOSE 7860

CMD ["uvicorn", "hindu_extract.api.main:app", "--host", "0.0.0.0", "--port", "7860"]
