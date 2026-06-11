FROM python:3.11-slim
WORKDIR /app
COPY agents/13_parlay_generator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY infrastructure/hermes /app/infrastructure/hermes
COPY agents/13_parlay_generator /app/agents/13_parlay_generator
ENV PYTHONPATH=/app
CMD ["python", "agents/13_parlay_generator/main.py"]
