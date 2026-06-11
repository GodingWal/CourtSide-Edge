FROM python:3.11-slim
WORKDIR /app
COPY agents/25_claim_verifier/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shared /app/shared
COPY agents/25_claim_verifier /app/agents/25_claim_verifier
ENV PYTHONPATH=/app
CMD ["python", "agents/25_claim_verifier/main.py"]
