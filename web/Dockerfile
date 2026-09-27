# Build context is the repo root (see docker-compose.yml), so both the web layer
# and the CLI core (filigranez.py) are available to COPY.
FROM python:3.12-slim

# poppler for rendering; Liberation fonts because --gouv is fitted to that face.
RUN apt-get update && apt-get install -y --no-install-recommends \
        poppler-utils fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt /app/requirements.txt
COPY web/requirements.txt /app/requirements-web.txt
RUN pip install --no-cache-dir -r /app/requirements.txt -r /app/requirements-web.txt

COPY filigranez.py /app/filigranez.py
COPY web /app/web

WORKDIR /app/web

# Port configurable (evite un conflit d'hote, ex. Portainer sur 8000).
ENV PORT=8010
EXPOSE 8010

# 2 workers; long timeout for big files at high DPI (100 MB cap).
# sh -c to expand ${PORT}; exec so gunicorn is PID 1 and gets signals.
CMD ["sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT} --workers 2 \
     --timeout 300 --access-logfile - --error-logfile - webapp:app"]
