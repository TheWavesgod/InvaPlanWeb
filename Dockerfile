FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN npm install --global pnpm@11.25.0 && pnpm install --frozen-lockfile
COPY index.html tsconfig.json vite.config.js ./
COPY src ./src
RUN pnpm build

FROM python:3.14-slim-bookworm AS local
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INVAPLAN_RELEASE_DIR=/release
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY datasets ./datasets
COPY --from=frontend /build/dist ./dist
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8765"]

FROM local AS hosted
RUN apt-get update && apt-get install -y --no-install-recommends openssh-sftp-server \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --user-group --shell /bin/bash invaplan \
    && install -d -m 0700 -o invaplan -g invaplan /home/invaplan/.ssh \
    && install -d -m 0750 -o invaplan -g invaplan /var/data
USER invaplan
CMD ["sh", "-c", "exec python -m uvicorn backend.hosted:create_app --factory --host 0.0.0.0 --port \"${PORT:-8765}\""]
