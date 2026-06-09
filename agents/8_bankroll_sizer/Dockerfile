FROM python:3.11-slim
WORKDIR /app
COPY agents/8_bankroll_sizer/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/8_bankroll_sizer /app/agents/8_bankroll_sizer
ENV PYTHONPATH=/app
CMD ["python", "agents/8_bankroll_sizer/main.py"]
