# Production image for the FastAPI server, also reused to run the background
# worker (same image, different CMD — see docker-compose.yml's "worker" service),
# so the API and worker always stay in lockstep on dependencies/code. ffmpeg and
# mupdf are native deps needed for audio + PDF processing at runtime. We only
# copy in the server/ subtree, then drop root and run uvicorn as a non-root
# user for basic container hardening.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libmupdf-dev \
    mupdf-tools \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY server/pyproject.toml ./
COPY server/app ./app

RUN pip install --upgrade pip && pip install .

RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
