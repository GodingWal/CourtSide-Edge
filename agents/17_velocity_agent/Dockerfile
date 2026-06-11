FROM python:3.11-slim

WORKDIR /app

COPY agents/17_velocity_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/17_velocity_agent /app/agents/17_velocity_agent

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8013

ENV PYTHONPATH=/app

CMD ["python", "agents/17_velocity_agent/main.py"]
