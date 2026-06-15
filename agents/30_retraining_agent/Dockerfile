FROM python:3.11-slim
WORKDIR /app
COPY agents/30_retraining_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/30_retraining_agent /app/agents/30_retraining_agent
ENV PYTHONPATH=/app
CMD ["python", "agents/30_retraining_agent/main.py"]
