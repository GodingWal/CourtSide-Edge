FROM python:3.11-slim

WORKDIR /app

COPY agents/18_liquidity_oracle/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/18_liquidity_oracle/ /app/

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8014

CMD ["python", "main.py"]
