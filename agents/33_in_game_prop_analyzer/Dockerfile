FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
COPY shared/ ./shared/
ENV PYTHONPATH=/app
EXPOSE 8033
CMD ["python", "-m", "agents.33_in_game_prop_analyzer.main"]
