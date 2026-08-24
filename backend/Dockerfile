# Hermes Investment Office Backend 镜像（生产形态，M5 起启用）
FROM python:3.12-slim

WORKDIR /srv/hermes

COPY pyproject.toml ./
COPY requirements.lock ./
COPY app ./app
COPY migrations ./migrations
COPY config ./config
COPY alembic.ini ./

RUN pip install --no-cache-dir -r requirements.lock \
    && pip install --no-cache-dir --no-deps .

EXPOSE 8000

# The container is allowed to migrate only its configured local compose DB.
# Production deployments should still review the migration plan before rollout.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
