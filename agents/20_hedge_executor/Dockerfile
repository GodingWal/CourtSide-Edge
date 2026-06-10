FROM python:3.11-slim

WORKDIR /app

COPY agents/20_hedge_executor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/20_hedge_executor/ /app/

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8016

CMD ["python", "main.py"]
