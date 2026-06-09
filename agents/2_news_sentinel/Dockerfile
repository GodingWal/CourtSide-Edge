FROM python:3.11-slim
WORKDIR /app
COPY agents/2_news_sentinel/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY infrastructure/nemotron /app/infrastructure/nemotron
COPY agents/2_news_sentinel /app/agents/2_news_sentinel
ENV PYTHONPATH=/app
CMD ["python", "agents/2_news_sentinel/main.py"]
