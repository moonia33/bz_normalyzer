FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN DJANGO_DEBUG=false DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput \
    && useradd --system --uid 1000 --home-dir /app app \
    && mkdir -p /app/media \
    && chown -R app:app /app/media \
    && chmod +x /app/docker-entrypoint.sh

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz/', timeout=4)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "300", "--access-logfile", "-", "--no-control-socket", "--forwarded-allow-ips", "*"]
