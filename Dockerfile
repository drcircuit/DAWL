FROM docker.io/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 TOKENIZERS_PARALLELISM=false
WORKDIR /app
COPY requirements-ingestion.txt .
RUN pip install --no-cache-dir -r requirements-ingestion.txt && useradd --uid 1000 --create-home dawl
COPY src ./src
COPY dawl_node.py .
USER 1000:1000
EXPOSE 8765 8766
ENTRYPOINT ["python", "dawl_node.py"]
