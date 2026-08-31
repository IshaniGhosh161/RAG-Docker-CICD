import logging
import os

from typing import List
from sentence_transformers import SentenceTransformer


logger = logging.getLogger(__name__)


class NomicEmbeddings:
    """
    LangChain-compatible wrapper around
    nomic-ai/nomic-embed-text-v1.5.

    Documents:
        search_document: <text>

    Queries:
        search_query: <text>
    """

    def __init__(
        self,
        model_name=None,
        device=None,
        batch_size=None,
    ):
        self.model_name = model_name or os.getenv(
            "HF_EMBEDDING_MODEL",
            "nomic-ai/nomic-embed-text-v1.5",
        )

        self.device = device or os.getenv(
            "HF_EMBEDDING_DEVICE",
            "cpu",
        )

        self.batch_size = batch_size or int(
            os.getenv(
                "HF_EMBEDDING_BATCH_SIZE",
                "8",
            )
        )

        logger.info(
            "Loading embedding model: %s",
            self.model_name,
        )

        logger.info(
            "Embedding device: %s",
            self.device,
        )

        self.model = SentenceTransformer(
            self.model_name,
            device=self.device,
            trust_remote_code=True,
        )

        logger.info(
            "Embedding model loaded successfully",
        )

    def embed_documents(
        self,
        texts,
    ):
        """
        Generate document embeddings.

        Nomic requires:
            search_document:
        """

        prefixed_texts = [
            "search_document: " + text.strip()
            for text in texts
        ]

        embeddings = self.model.encode(
            prefixed_texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=True,
        )

        return embeddings.tolist()

    def embed_query(
        self,
        text,
    ):
        """
        Generate query embedding.

        Nomic requires:
            search_query:
        """

        query = (
            "search_query: "
            + text.strip()
        )

        embedding = self.model.encode(
            query,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )

        return embedding.tolist()
    
    def __call__( self, text: str) -> List[float]: 
        """ Make the object callable. Older/current 
        versions of langchain-community FAISS may 
        call the embedding function directly: 
        embedding_function(text) 
        Therefore this must delegate to embed_query(). 
        """ 
        return self.embed_query(text)