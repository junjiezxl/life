# Stage 1: Build frontend static assets
FROM node:22-alpine AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Runtime image
FROM python:3.11-slim

ARG LIFEHUB_VERSION=v1.0.0-dev
ARG LIFEHUB_BUILD_SHA=dev
ARG LIFEHUB_BUILD_TIME=
ARG LIFEHUB_BUILD_MSG=

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    LIFEHUB_VERSION=${LIFEHUB_VERSION} \
    LIFEHUB_BUILD_SHA=${LIFEHUB_BUILD_SHA} \
    LIFEHUB_BUILD_TIME=${LIFEHUB_BUILD_TIME} \
    LIFEHUB_BUILD_MSG=${LIFEHUB_BUILD_MSG}

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime \
    && echo "$TZ" > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app ./app
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

RUN mkdir -p /app/backend/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
