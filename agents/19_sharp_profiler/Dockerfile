FROM python:3.11-slim

WORKDIR /app

COPY agents/19_sharp_profiler/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/19_sharp_profiler/ /app/

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8015

CMD ["python", "main.py"]
