FROM python:3.11-slim

WORKDIR /app

COPY agents/16_hedge_oracle/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/16_hedge_oracle /app/agents/16_hedge_oracle

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8012

ENV PYTHONPATH=/app

CMD ["python", "agents/16_hedge_oracle/main.py"]
