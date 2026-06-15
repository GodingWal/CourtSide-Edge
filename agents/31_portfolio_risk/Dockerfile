FROM python:3.11-slim
WORKDIR /app
COPY agents/31_portfolio_risk/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/31_portfolio_risk /app/agents/31_portfolio_risk
ENV PYTHONPATH=/app
CMD ["python", "agents/31_portfolio_risk/main.py"]
