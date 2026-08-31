import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import FAISS

# Add project root to Python path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load environment variables
load_dotenv()

# Logging
try:
    from backend import logging_config
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
    )

logger = logging.getLogger(__name__)

# Shared Nomic embedding implementation
from backend.embeddings import NomicEmbeddings


PDF_FOLDER = PROJECT_ROOT / "data"

FAISS_INDEX = (
    PROJECT_ROOT
    / "index"
    / "faiss_index"
)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200


logger.info("==============================================")
logger.info("Loading PDF documents")
logger.info("==============================================")

if not PDF_FOLDER.exists():
    raise FileNotFoundError(
        f"PDF directory does not exist: {PDF_FOLDER}"
    )

pdf_files = sorted(
    PDF_FOLDER.glob("*.pdf")
)

if not pdf_files:
    raise FileNotFoundError(
        f"No PDF files found in: {PDF_FOLDER}"
    )

logger.info(
    "Found %d PDF file(s)",
    len(pdf_files),
)

documents = []

for pdf_file in pdf_files:

    logger.info(
        "Loading: %s",
        pdf_file.name,
    )

    loader = PyPDFLoader(
        str(pdf_file)
    )

    pdf_documents = loader.load()

    documents.extend(
        pdf_documents
    )

logger.info(
    "Loaded %d page(s)",
    len(documents),
)


logger.info("==============================================")
logger.info("Splitting documents")
logger.info("==============================================")

text_splitter = RecursiveCharacterTextSplitter(
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    separators=[
        "\n\n",
        "\n",
        ". ",
        " ",
        "",
    ],
)

chunks = text_splitter.split_documents(
    documents
)

logger.info(
    "Created %d chunks",
    len(chunks),
)


logger.info("==============================================")
logger.info("Initializing Nomic embeddings")
logger.info("==============================================")

embeddings = NomicEmbeddings()

logger.info(
    "Nomic embeddings initialized successfully"
)


logger.info("==============================================")
logger.info("Creating FAISS index")
logger.info("==============================================")

vector_store = FAISS.from_documents(
    documents=chunks,
    embedding=embeddings,
)

logger.info(
    "FAISS index created successfully"
)


logger.info("==============================================")
logger.info("Saving FAISS index")
logger.info("==============================================")

FAISS_INDEX.parent.mkdir(
    parents=True,
    exist_ok=True,
)

vector_store.save_local(
    str(FAISS_INDEX)
)

logger.info(
    "FAISS index saved to: %s",
    FAISS_INDEX,
)


logger.info("==============================================")
logger.info("INDEX CREATION COMPLETE")
logger.info("==============================================")

logger.info(
    "Documents: %d",
    len(documents),
)

logger.info(
    "Chunks: %d",
    len(chunks),
)

logger.info(
    "FAISS location: %s",
    FAISS_INDEX,
)