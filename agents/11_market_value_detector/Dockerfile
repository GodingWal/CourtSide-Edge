FROM python:3.11-slim
WORKDIR /app
COPY agents/11_market_value_detector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/11_market_value_detector /app/agents/11_market_value_detector
ENV PYTHONPATH=/app
CMD ["python", "agents/11_market_value_detector/main.py"]
