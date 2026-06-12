FROM python:3.11-slim
WORKDIR /app
COPY agents/24_validation_gate/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/24_validation_gate /app/agents/24_validation_gate
ENV PYTHONPATH=/app
CMD ["python", "agents/24_validation_gate/main.py"]
