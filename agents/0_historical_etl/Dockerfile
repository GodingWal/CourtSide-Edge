FROM python:3.11-slim
WORKDIR /app
COPY agents/0_historical_etl/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/0_historical_etl /app/agents/0_historical_etl
ENV PYTHONPATH=/app
CMD ["python", "agents/0_historical_etl/main.py"]
