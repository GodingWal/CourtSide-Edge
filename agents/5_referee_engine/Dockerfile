FROM python:3.11-slim
WORKDIR /app
COPY agents/5_referee_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/5_referee_engine /app/agents/5_referee_engine
ENV PYTHONPATH=/app
CMD ["python", "agents/5_referee_engine/main.py"]
