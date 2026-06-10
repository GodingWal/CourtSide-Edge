FROM python:3.11-slim

WORKDIR /app

COPY agents/15_drift_monitor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/15_drift_monitor/ /app/

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8011

CMD ["python", "main.py"]
