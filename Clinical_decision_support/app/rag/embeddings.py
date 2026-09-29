from sklearn.feature_extraction.text import TfidfVectorizer

from app.core.config import EMBEDDING_MODEL


_model = None


def get_embedding_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBEDDING_MODEL)
    return _model


def generate_sentence_embeddings(texts, batch_size):
    return get_embedding_model().encode(
        texts,
        batch_size=batch_size,
        show_progress_bar=True,
        normalize_embeddings=True,
        convert_to_numpy=True,
    )


def generate_tfidf_embeddings(texts):
    vectorizer = create_vectorizer()
    return vectorizer, vectorizer.fit_transform(texts)


def create_vectorizer():
    """
    Create TF-IDF vectorizer for local semantic-like text retrieval.
    This avoids Hugging Face, Gemini, and external model downloads.
    """
    return TfidfVectorizer(
        stop_words="english",
        max_features=5000,
        ngram_range=(1, 2)
    )


def generate_embeddings(texts):
    """
    Kept for compatibility with build_kb.py.
    Returns TF-IDF matrix and vectorizer.
    """
    vectorizer = create_vectorizer()
    matrix = vectorizer.fit_transform(texts)

    return vectorizer, matrix
