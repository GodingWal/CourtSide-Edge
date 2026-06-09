FROM python:3.11-slim
WORKDIR /app
COPY agents/1_market_scraper/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/1_market_scraper /app/agents/1_market_scraper
ENV PYTHONPATH=/app
CMD ["python", "agents/1_market_scraper/main.py"]
