# FaxxMe — container image
# Build:  docker build -t faxxme .
# Run:    docker run -p 8000:8000 -v faxxme-data:/data faxxme
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FAXXME_HOST=0.0.0.0 \
    FAXXME_PORT=8000 \
    FAXXME_DB=/data/faxxme.db \
    FAXXME_SECRET=/data/.faxxme_secret

WORKDIR /app

# deps first for layer caching (Pillow/uvicorn install from wheels, no build tools needed)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY faxxme ./faxxme
COPY static ./static

# non-root runtime user, added to lp for the optional USB printer bridge.
# /data holds the sqlite db + session secret — mount a volume there.
RUN useradd --system --uid 10001 --gid users --groups lp app \
    && mkdir -p /data && chown -R app:users /data
VOLUME ["/data"]
USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=3).status==200 else 1)"

CMD ["python", "-m", "faxxme"]
