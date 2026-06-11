FROM python:3.11-slim

WORKDIR /app

COPY agents/19_sharp_profiler/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/19_sharp_profiler /app/agents/19_sharp_profiler

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8015

ENV PYTHONPATH=/app

CMD ["python", "agents/19_sharp_profiler/main.py"]
