FROM python:3.11-slim
WORKDIR /app
COPY agents/27_rejection_triage/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY infrastructure/hermes /app/infrastructure/hermes
COPY agents/27_rejection_triage /app/agents/27_rejection_triage
ENV PYTHONPATH=/app
CMD ["python", "agents/27_rejection_triage/main.py"]
