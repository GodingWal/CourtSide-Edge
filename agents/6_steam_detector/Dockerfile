FROM python:3.11-slim
WORKDIR /app
COPY agents/6_steam_detector/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/6_steam_detector /app/agents/6_steam_detector
ENV PYTHONPATH=/app
CMD ["python", "agents/6_steam_detector/main.py"]
