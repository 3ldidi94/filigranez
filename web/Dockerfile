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
# BIND=127.0.0.1 par defaut: en network_mode host, seul le reverse proxy
# local (nginx) atteint le service, jamais le LAN directement. Mettre
# BIND=0.0.0.0 uniquement en reseau bridge avec mapping de ports.
ENV PORT=8010
ENV BIND=127.0.0.1
EXPOSE 8010

# 2 workers; long timeout for big files at high DPI (100 MB cap).
# sh -c to expand ${PORT}; exec so gunicorn is PID 1 and gets signals.
CMD ["sh", "-c", "exec gunicorn --bind ${BIND}:${PORT} --workers 2 \
     --timeout 300 --access-logfile - --error-logfile - webapp:app"]
