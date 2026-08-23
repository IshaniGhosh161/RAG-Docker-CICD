import os
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_ollama import OllamaEmbeddings
from langchain_community.vectorstores import FAISS
import logging_config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Configuration
PDF_FOLDER = PROJECT_ROOT / "data"
FAISS_INDEX = PROJECT_ROOT / "index" / "faiss_index"

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200

EMBEDDING_MODEL = "nomic-embed-text"

# Load all PDFs
documents = []

pdf_files = list(Path(PDF_FOLDER).glob("*.pdf"))

logger.info("Found %d PDF(s)", len(pdf_files))

for pdf in pdf_files:
    logger.info("Loading: %s", pdf.name)

    loader = PyPDFLoader(str(pdf))
    docs = loader.load()

    documents.extend(docs)

logger.info("Loaded %d pages", len(documents))

# Split into chunks
text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=[
        "\n\n",
        "\n",
        ". ",
        " ",
        ""
    ]
)

chunks = text_splitter.split_documents(documents)

logger.info("Generated %d chunks", len(chunks))

# Embedding Model
embeddings = OllamaEmbeddings(
    model=EMBEDDING_MODEL
)

logger.info("Embedding model loaded")

# Create FAISS Index

vector_store = FAISS.from_documents(
    documents=chunks,
    embedding=embeddings
)

logger.info("Embeddings generated")

# Save Index
vector_store.save_local(FAISS_INDEX)

logger.info("FAISS index saved to '%s'", FAISS_INDEX)
logger.info("Done")