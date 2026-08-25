# Hermes Investment Office Backend 镜像（生产形态，M5 起启用）
FROM python:3.12-slim

WORKDIR /srv/hermes

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
COPY config ./config
COPY alembic.ini ./

RUN pip install --no-cache-dir --no-deps .

EXPOSE 8000

# 容器仅迁移 compose 中明确配置的本机数据库；正式迁移仍需先审阅变更。
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000"]
