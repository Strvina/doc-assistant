# Doc Assistant: one image serves the web UI and the REST API (FastAPI + uvicorn).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    EMBEDDING_CACHE_DIR=/app/models \
    INDEX_DIR=/state/index \
    DOCUMENTS_DIR=/state/documents \
    LOG_DB=/state/logs.sqlite \
    OUTBOX_DIR=/state/outbox \
    INBOX_DIR=/state/inbox \
    PORT=8000

WORKDIR /app

# OCR for scans and text in images (ADR-024): Tesseract with Serbian, German and English.
RUN apt-get update     && apt-get install -y --no-install-recommends tesseract-ocr        tesseract-ocr-srp tesseract-ocr-srp-latn tesseract-ocr-deu tesseract-ocr-eng     && rm -rf /var/lib/apt/lists/*

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY eval ./eval
COPY data/sample_docs ./data/sample_docs
RUN uv sync --locked --no-dev

# Bake the embedding model into the image: containers start fast and work offline.
RUN python -c "from docassist.retrieval import LocalEmbedder; LocalEmbedder(cache_dir='/app/models')"

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /state \
    && chown -R app /state /app/data
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/health', timeout=4)"
# PORT is configurable because some hosts (e.g. Hugging Face Spaces: 7860) choose it.
CMD ["sh", "-c", "uvicorn docassist.api:app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
