FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8090

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Run as non-root user for Cloud Run security compliance
RUN useradd -m -u 10001 agentuser && chown -R agentuser:agentuser /app
USER agentuser

EXPOSE 8090

CMD ["python", "dashboard_server.py"]
