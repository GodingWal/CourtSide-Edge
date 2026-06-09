FROM python:3.11-slim
WORKDIR /app
COPY agents/3_projection_engine/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/3_projection_engine /app/agents/3_projection_engine
ENV PYTHONPATH=/app
CMD ["python", "agents/3_projection_engine/main.py"]
