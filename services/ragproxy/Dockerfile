FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt /app/requirements.txt

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc wget && \
    pip install --no-cache-dir -r /app/requirements.txt && \
    apt-get purge -y --auto-remove gcc && \
    rm -rf /var/lib/apt/lists/*

COPY app.py /app/app.py

EXPOSE 4080
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "4080"]
