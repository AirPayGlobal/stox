FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Dashboard + engine control API. Start the engine from the UI or POST /api/start.
# JSON form (satisfies Docker's JSONArgsRecommended) with an explicit shell so
# ${PORT} (set by Railway/Heroku-style platforms) is expanded, and `exec` so
# uvicorn replaces sh as PID 1 and receives SIGTERM for graceful shutdown.
CMD ["sh", "-c", "exec uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
