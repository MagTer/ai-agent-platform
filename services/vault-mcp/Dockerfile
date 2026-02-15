FROM python:3.12-alpine

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application files
COPY server.py .
COPY couch_client.py .

# Run server
CMD ["python", "server.py"]
