# 前端构建：产物输出到 ../backend/evalp/static（见 frontend/vite.config.ts）
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/ ./backend/
COPY --from=frontend /src/backend/evalp/static/ ./backend/evalp/static/
RUN pip install --no-cache-dir ./backend && rm -rf ./backend

RUN useradd --create-home --uid 1000 evalp && mkdir /data && chown evalp:evalp /data
USER evalp
ENV EVALP_DATA_DIR=/data \
    EVALP_SERVER_HOST=0.0.0.0 \
    EVALP_SERVER_ALLOW_REMOTE_BOOTSTRAP=true
VOLUME /data
EXPOSE 7860
CMD ["evalp", "serve"]
