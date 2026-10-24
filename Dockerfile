FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

RUN useradd -r -u 1000 appuser && mkdir -p /tmp/backups && chown appuser /tmp/backups
USER appuser

CMD ["python", "-m", "app.main"]
