"""Embedding providers. Both implement LangChain's `Embeddings` interface, so any LangChain
component (or RAGAS) can reuse them.

- `HashingEmbeddings` (EMBEDDING_PROVIDER=hashing, default): works fully offline with no model
  download. It hashes character n-grams, so it matches SPELLING, not meaning. Arabic words are
  also mapped to English canonical terms through the project's bilingual synonym list, which
  lets simple Arabic questions find English passages. Good for demos and tests; weak on
  paraphrases.
- `SentenceTransformerEmbeddings` (EMBEDDING_PROVIDER=sentence_transformers): a real
  multilingual semantic model (default intfloat/multilingual-e5-small), running locally on CPU.
  Needs `pip install sentence-transformers` and a one-time model download from Hugging Face.
"""
from __future__ import annotations

from langchain_core.embeddings import Embeddings

from src.config import Settings
from src.knowledge_base.text_utils import normalize_arabic, tokenize


class EmbeddingError(Exception):
    """Raised when an embedding model cannot be loaded or used."""


class HashingEmbeddings(Embeddings):
    def __init__(self, n_features: int = 2048) -> None:
        from sklearn.feature_extraction.text import HashingVectorizer

        self._vectorizer = HashingVectorizer(analyzer="char_wb", ngram_range=(3, 5), n_features=n_features,
                                             alternate_sign=False, norm="l2")
        self.model_id = f"hashing-char3to5-{n_features}"

    @staticmethod
    def _prepare(text: str) -> str:
        # Add canonical English terms (e.g. "اختبار" -> "testing") as a cheap bilingual bridge.
        return f"{normalize_arabic(text.lower())} {' '.join(tokenize(text))}"

    def _embed(self, texts: list[str]) -> list[list[float]]:
        matrix = self._vectorizer.transform([self._prepare(t) for t in texts])
        return matrix.toarray().tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class SentenceTransformerEmbeddings(Embeddings):
    def __init__(self, model_name: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingError("Install sentence-transformers: pip install sentence-transformers") from exc
        try:
            self._model = SentenceTransformer(model_name)
        except Exception as exc:
            raise EmbeddingError(f"Could not load embedding model '{model_name}' ({type(exc).__name__}). "
                                 "The first run needs internet access to download it.") from exc
        self.model_id = model_name
        # E5 models were trained with these prefixes; using them noticeably improves retrieval.
        is_e5 = "e5" in model_name.lower()
        self._query_prefix, self._doc_prefix = ("query: ", "passage: ") if is_e5 else ("", "")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode([self._doc_prefix + t for t in texts], normalize_embeddings=True).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([self._query_prefix + text], normalize_embeddings=True)[0].tolist()


def create_embeddings(settings: Settings) -> Embeddings:
    if settings.embedding_provider == "sentence_transformers":
        return SentenceTransformerEmbeddings(settings.embedding_model)
    return HashingEmbeddings()


def embedding_model_id(embeddings: Embeddings) -> str:
    return getattr(embeddings, "model_id", type(embeddings).__name__)
