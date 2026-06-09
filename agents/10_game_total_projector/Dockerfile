FROM python:3.11-slim
WORKDIR /app
COPY agents/10_game_total_projector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/10_game_total_projector /app/agents/10_game_total_projector
ENV PYTHONPATH=/app
CMD ["python", "agents/10_game_total_projector/main.py"]
