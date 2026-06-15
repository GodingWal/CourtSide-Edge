FROM python:3.11-slim
WORKDIR /app
COPY agents/29_backtest_agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/29_backtest_agent /app/agents/29_backtest_agent
ENV PYTHONPATH=/app
CMD ["python", "agents/29_backtest_agent/main.py"]
