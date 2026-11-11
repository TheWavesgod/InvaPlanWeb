FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN npm install --global pnpm@11.25.0 && pnpm install --frozen-lockfile
COPY index.html tsconfig.json vite.config.js ./
COPY src ./src
RUN pnpm build

FROM python:3.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 INVAPLAN_RELEASE_DIR=/release
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend ./backend
COPY datasets ./datasets
COPY --from=frontend /build/dist ./dist
EXPOSE 8765
CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8765"]
