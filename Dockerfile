# Doc Assistant: one image for the Streamlit UI (default) and the REST API.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.13.0 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH" \
    EMBEDDING_CACHE_DIR=/app/models \
    INDEX_DIR=/state/index \
    LOG_DB=/state/logs.sqlite \
    OUTBOX_DIR=/state/outbox

WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project

COPY src ./src
COPY app ./app
COPY eval ./eval
COPY data/sample_docs ./data/sample_docs
RUN uv sync --locked --no-dev

# Bake the embedding model into the image: containers start fast and work offline.
RUN python -c "from docassist.retrieval import LocalEmbedder; LocalEmbedder(cache_dir='/app/models')"

RUN useradd --create-home app \
    && mkdir -p /state /app/data/uploads \
    && chown -R app /state /app/data
USER app

EXPOSE 8501 8000
CMD ["streamlit", "run", "app/streamlit_app.py", \
     "--server.address=0.0.0.0", "--server.headless=true", "--browser.gatherUsageStats=false"]
