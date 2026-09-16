import os
from dotenv import load_dotenv

load_dotenv()

# RAG Settings
RETRIEVAL_K = int(os.getenv("RETRIEVAL_K", 10))
RERANK_TOP_N = int(os.getenv("RERANK_TOP_N", 5))
ENABLE_RERANKER = os.getenv("ENABLE_RERANKER", "True").lower() == "true"
FAST_MODE = os.getenv("FAST_MODE", "False").lower() == "true"

# Ollama Settings
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "https://ollama.com")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gpt-oss:20b-cloud")
OLLAMA_API_KEY = os.getenv("OLLAMA_API_KEY", "")

# Embedding Settings
HF_EMBEDDING_MODEL = os.getenv("HF_EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")
HF_EMBEDDING_DEVICE = os.getenv("HF_EMBEDDING_DEVICE", "cpu")
HF_EMBEDDING_BATCH_SIZE = int(os.getenv("HF_EMBEDDING_BATCH_SIZE", "8"))

# Other API Keys
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
MONGO_URI = os.getenv("MONGO_URI", "")

# Logging Settings
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "api.log")
