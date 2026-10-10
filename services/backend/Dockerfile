FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY central_monitor ./central_monitor
COPY tests ./tests

RUN useradd --create-home --uid 10001 appuser
USER appuser

CMD ["uvicorn", "central_monitor.main:app", "--host", "0.0.0.0", "--port", "8000"]

