# syntax=docker/dockerfile:1

# --- 1. сборка фронтенда ---
FROM node:22-alpine AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- 2. python-зависимости ---
FROM python:3.12-slim AS pydeps
WORKDIR /build
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# --- 3. итоговый образ ---
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MESHBOARD_HOST=0.0.0.0 \
    MESHBOARD_PORT=8080 \
    MESHBOARD_DATA_DIR=/data \
    MESHBOARD_STATIC_DIR=/app/static
# пользователь без root; группа dialout — доступ к USB-serial
RUN useradd --system --uid 10001 --gid dialout --home-dir /app --shell /usr/sbin/nologin meshboard \
    && mkdir -p /data && chown meshboard:dialout /data
COPY --from=pydeps /install /usr/local
COPY backend/app /app/app
COPY --from=frontend /src/dist /app/static
WORKDIR /app
USER meshboard
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('MESHBOARD_PORT','8080'), timeout=4)" || exit 1
CMD ["python", "-m", "app.main"]
