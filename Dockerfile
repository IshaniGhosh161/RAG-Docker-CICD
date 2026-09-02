FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    HF_HUB_CACHE=/opt/huggingface/hub

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

# Pre-download Hugging Face models during image build.
# This prevents the first API request from waiting for model downloads.
RUN python -c "\
from sentence_transformers import SentenceTransformer; \
SentenceTransformer('nomic-ai/nomic-embed-text-v1.5', device='cpu', trust_remote_code=True); \
SentenceTransformer('cross-encoder/ms-marco-MiniLM-L-6-v2', device='cpu')"

# Copy application source and pre-built FAISS index
COPY . .

RUN mkdir -p /app/log

EXPOSE 5000

HEALTHCHECK \
    --interval=30s \
    --timeout=10s \
    --start-period=120s \
    --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/health')"

CMD ["uvicorn", "backend.api:app", "--host", "0.0.0.0", "--port", "5000", "--workers", "1"]