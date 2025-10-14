# syntax=docker/dockerfile:1

########################
# Builder stage
########################
FROM python:3.11-slim AS builder

WORKDIR /app

# Rendszerfüggőségek a buildhez
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
 && rm -rf /var/lib/apt/lists/*

# >>> FONTOS: a requirements az api/app alatt van <<<
COPY api/app/requirements.txt ./requirements.txt

RUN pip install --upgrade pip && \
    pip wheel --wheel-dir /wheels -r requirements.txt

########################
# Runtime stage
########################
FROM python:3.11-slim

WORKDIR /app

# Kerekek másolása és telepítése
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*

# >>> Itt másoljuk be a tényleges appot az api/app-ból <<<
COPY api/app ./app

# (opcionális) környezeti változók
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

EXPOSE 8000

# Uvicorn indítás
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

