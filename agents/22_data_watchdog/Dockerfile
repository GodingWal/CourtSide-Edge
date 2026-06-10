FROM python:3.11-slim
WORKDIR /app
COPY agents/22_data_watchdog/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/22_data_watchdog /app/agents/22_data_watchdog
ENV PYTHONPATH=/app
CMD ["python", "agents/22_data_watchdog/main.py"]
