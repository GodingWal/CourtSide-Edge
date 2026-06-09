FROM python:3.11-slim
WORKDIR /app
COPY agents/9_news_sentiment/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY infrastructure/nemotron /app/infrastructure/nemotron
COPY agents/9_news_sentiment /app/agents/9_news_sentiment
ENV PYTHONPATH=/app
CMD ["python", "agents/9_news_sentiment/main.py"]
