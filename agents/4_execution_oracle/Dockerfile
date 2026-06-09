FROM python:3.11-slim
WORKDIR /app
COPY agents/4_execution_oracle/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/4_execution_oracle /app/agents/4_execution_oracle
ENV PYTHONPATH=/app
CMD ["python", "agents/4_execution_oracle/main.py"]
