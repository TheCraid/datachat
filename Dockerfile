FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
RUN useradd -m -u 1000 app
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Download the embedding model for the vector example memory at build time, so the app never fetches it at runtime.
ENV EMBEDDING_CACHE_DIR=/app/models
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models')"

COPY . .
RUN chown -R app:app /app
USER app

EXPOSE 7860
# Render (and most hosts) pass the port in $PORT; 7860 is the local default.
CMD ["sh", "-c", "uvicorn app.api:app --host 0.0.0.0 --port ${PORT:-7860}"]
