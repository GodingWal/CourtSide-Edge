FROM python:3.11-slim

WORKDIR /app

COPY agents/21_rotation_tracker/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shared/ /app/shared/
COPY agents/21_rotation_tracker/ /app/

ENV REDIS_HOST=redis
ENV REDIS_PORT=6379

EXPOSE 8017

CMD ["python", "main.py"]
