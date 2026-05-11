FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data

WORKDIR /app

RUN useradd --create-home --uid 1000 appuser

COPY requirements.txt .
RUN pip install --no-cache-dir --no-compile -r requirements.txt

COPY app ./app

RUN mkdir -p /data && chown appuser:appuser /data

USER appuser

EXPOSE 17000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "17000"]
