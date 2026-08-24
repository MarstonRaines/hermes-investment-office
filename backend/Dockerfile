# Hermes Investment Office Backend 镜像（生产形态，M5 起启用）
FROM python:3.12-slim

WORKDIR /srv/hermes

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY config ./config
COPY alembic.ini ./

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
