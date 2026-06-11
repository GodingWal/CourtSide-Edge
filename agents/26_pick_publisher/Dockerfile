FROM python:3.11-slim
WORKDIR /app
COPY agents/26_pick_publisher/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/26_pick_publisher /app/agents/26_pick_publisher
ENV PYTHONPATH=/app
CMD ["python", "agents/26_pick_publisher/main.py"]
