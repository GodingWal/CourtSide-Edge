FROM python:3.11-slim
WORKDIR /app
COPY agents/7_correlation_guard/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/7_correlation_guard /app/agents/7_correlation_guard
ENV PYTHONPATH=/app
CMD ["python", "agents/7_correlation_guard/main.py"]
