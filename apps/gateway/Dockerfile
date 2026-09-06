# RingFence ingest gateway (T-6.2).
FROM python:3.13-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
        libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY packages ./packages
COPY apps ./apps
COPY config ./config
COPY corpus/fixtures ./corpus/fixtures

RUN pip install --no-cache-dir -e .

EXPOSE 8000
ENV RF_DRY_RUN=true
CMD ["uvicorn", "apps.gateway.main:app", "--host", "0.0.0.0", "--port", "8000"]
