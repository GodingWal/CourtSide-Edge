FROM python:3.11-slim
WORKDIR /app
COPY agents/2.5_game_flow_oracle/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/2.5_game_flow_oracle /app/agents/2.5_game_flow_oracle
ENV PYTHONPATH=/app
CMD ["python", "agents/2.5_game_flow_oracle/main.py"]
