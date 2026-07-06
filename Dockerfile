FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1
EXPOSE 8000

# Dashboard + engine control API. Start the engine from the UI or POST /api/start.
# Shell form so ${PORT} (set by Railway/Heroku-style platforms) is expanded.
CMD uvicorn api.server:app --host 0.0.0.0 --port ${PORT:-8000}
