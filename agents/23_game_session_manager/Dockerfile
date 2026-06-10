FROM python:3.11-slim
WORKDIR /app
COPY agents/23_game_session_manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/23_game_session_manager /app/agents/23_game_session_manager
ENV PYTHONPATH=/app
CMD ["python", "agents/23_game_session_manager/main.py"]
