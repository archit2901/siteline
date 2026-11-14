# Build from the repo root: docker build -f services/agent/Dockerfile .
FROM python:3.11-slim

WORKDIR /app
ENV PYTHONPATH=/app PYTHONUNBUFFERED=1

COPY services/agent/requirements.txt services/agent/requirements.txt
RUN pip install --no-cache-dir -r services/agent/requirements.txt

COPY contracts/ contracts/
COPY connectors/ connectors/
COPY services/__init__.py services/__init__.py
COPY services/agent/ services/agent/
COPY catalog/ catalog/
COPY packs/ packs/
COPY evals/ evals/
COPY fixtures/ fixtures/

EXPOSE 8000
CMD ["uvicorn", "services.agent.app:app", "--host", "0.0.0.0", "--port", "8000"]
