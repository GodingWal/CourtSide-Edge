FROM python:3.11-slim
WORKDIR /app
COPY agents/28_meta_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/28_meta_agent /app/agents/28_meta_agent
ENV PYTHONPATH=/app
CMD ["python", "agents/28_meta_agent/main.py"]
