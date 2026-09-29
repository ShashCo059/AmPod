import re
import pickle
import json

from scipy.sparse import load_npz
from sklearn.metrics.pairwise import cosine_similarity

from app.core.config import MAX_CONTEXT_CHARS
from app.core.config import KB_BACKEND_FILE
from app.core.config import KB_CHUNKS_FILE
from app.core.config import RETRIEVAL_CANDIDATES
from app.core.config import RETRIEVAL_RESULTS
from app.core.config import VECTOR_DB_MATRIX
from app.core.config import VECTOR_DB_VECTORIZER
from app.rag.embeddings import get_embedding_model
from app.rag.vector_store import collection


def _token_set(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def _deduplicate(chunks):
    selected = []
    selected_tokens = []
    for chunk in chunks:
        tokens = _token_set(chunk["text"])
        if any(len(tokens & previous) / max(len(tokens), 1) > 0.85 for previous in selected_tokens):
            continue
        selected.append(chunk)
        selected_tokens.append(tokens)
    return selected


def retrieve_chunks(query, candidates=RETRIEVAL_CANDIDATES, results=RETRIEVAL_RESULTS):
    backend = KB_BACKEND_FILE.read_text(encoding="utf-8").strip() if KB_BACKEND_FILE.exists() else "sentence_transformer"
    if backend == "tfidf":
        return _retrieve_tfidf(query, results)
    if collection.count() == 0:
        raise FileNotFoundError("Knowledge base is empty. Please run: python -m app.scripts.build_kb")

    query_embedding = get_embedding_model().encode(
        [query], normalize_embeddings=True, convert_to_numpy=True
    )[0].tolist()
    response = collection.query(
        query_embeddings=[query_embedding],
        n_results=max(candidates, results),
        include=["documents", "metadatas", "distances"],
    )
    documents = response.get("documents", [[]])[0]
    metadatas = response.get("metadatas", [[]])[0]
    distances = response.get("distances", [[]])[0]
    chunks = [
        {"text": text, "metadata": metadata or {}, "distance": distance}
        for text, metadata, distance in zip(documents, metadatas, distances)
    ]
    return _deduplicate(chunks)[:results]


def _retrieve_tfidf(query, results):
    if not VECTOR_DB_VECTORIZER.exists() or not VECTOR_DB_MATRIX.exists() or not KB_CHUNKS_FILE.exists():
        raise FileNotFoundError("TF-IDF knowledge-base artifacts are missing. Please rebuild the knowledge base.")
    with VECTOR_DB_VECTORIZER.open("rb") as file:
        vectorizer = pickle.load(file)
    matrix = load_npz(VECTOR_DB_MATRIX)
    scores = cosine_similarity(vectorizer.transform([query]), matrix).ravel()
    ids = scores.argsort()[::-1][:results]
    chunks = json.loads(KB_CHUNKS_FILE.read_text(encoding="utf-8"))
    chunks = [
        {"text": chunks[index]["text"], "metadata": chunks[index]["metadata"], "distance": 1 - scores[index]}
        for index in ids
        if scores[index] > 0
    ]
    return _deduplicate(chunks)[:results]


def retrieve_documents(query, top_k=5):
    chunks = retrieve_chunks(query, results=top_k)
    if not chunks:
        return "No highly relevant context found in the current knowledge base."
    context = []
    total_chars = 0
    for chunk in chunks:
        metadata = chunk["metadata"]
        if "page" in metadata:
            location = f"page {metadata['page']}"
        else:
            location = f"block {metadata.get('block', '?')}"
        heading = metadata.get("heading")
        if heading:
            location += f", section {heading}"
        citation = f"Source: {metadata.get('source', 'knowledge base')}, {location}"
        entry = f"{citation}\n{chunk['text']}"
        if total_chars + len(entry) > MAX_CONTEXT_CHARS:
            break
        context.append(entry)
        total_chars += len(entry)
    return "\n\n".join(context)
