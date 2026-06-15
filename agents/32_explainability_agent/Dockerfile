FROM python:3.11-slim
WORKDIR /app
COPY agents/32_explainability_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/32_explainability_agent /app/agents/32_explainability_agent
ENV PYTHONPATH=/app
CMD ["python", "agents/32_explainability_agent/main.py"]
