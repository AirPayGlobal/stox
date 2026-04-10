# =============================================================================
# Stage 1: Build the React dashboard
# =============================================================================
FROM node:20-alpine AS frontend

WORKDIR /build

COPY dashboard/package.json dashboard/package-lock.json* ./
RUN npm install --frozen-lockfile 2>/dev/null || npm install

COPY dashboard/ .
RUN npm run build


# =============================================================================
# Stage 2: Python runtime — FastAPI + trading bot
# =============================================================================
FROM python:3.11-slim

# System deps (pandas/numpy need a C compiler on slim images)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        g++ \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot source
COPY . .

# Copy the compiled React app from stage 1
COPY --from=frontend /build/dist ./dashboard/dist

# /data is the Railway Volume mount point — portfolio.json persists here across deploys.
# The RUN mkdir ensures the directory exists even without a volume (fallback to ephemeral).
RUN mkdir -p /data logs

EXPOSE 8000

# Uvicorn serves the FastAPI app; the bot is started via the dashboard
CMD ["sh", "-c", "uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
