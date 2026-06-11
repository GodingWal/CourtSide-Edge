FROM python:3.11-slim

WORKDIR /app

COPY agents/14_clv_tracker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/14_clv_tracker /app/agents/14_clv_tracker

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8010

ENV PYTHONPATH=/app

CMD ["python", "agents/14_clv_tracker/main.py"]
