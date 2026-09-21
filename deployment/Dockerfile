FROM python:3.13.15-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends -y age ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 jobapp \
    && useradd --uid 10001 --gid jobapp --home-dir /app --shell /usr/sbin/nologin jobapp

WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install --requirement requirements.txt
COPY . .
RUN mkdir -p /app/data /app/private /app/temporary /app/staticfiles /backups \
    && chown -R jobapp:jobapp /app /backups

USER 10001:10001
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind=0.0.0.0:8000", "--workers=2", "--threads=4", "--timeout=60", "--access-logfile=-", "--error-logfile=-"]
