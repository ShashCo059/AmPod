from app.rag.chunker import chunk_pages
from app.rag.retriever import _deduplicate


def test_chunk_pages_preserves_page_metadata():
    chunks = chunk_pages(
        [{"text": "Hypertension guidance.", "page": 12, "ocr_required": False}],
        "guidelines.pdf",
    )

    assert chunks[0]["metadata"] == {
        "source": "guidelines.pdf",
        "page": 12,
        "chunk": 1,
        "ocr_required": False,
    }


def test_deduplicate_removes_near_identical_chunks():
    chunks = [
        {"text": "Check blood pressure and repeat measurement.", "metadata": {}},
        {"text": "Check blood pressure and repeat measurement.", "metadata": {}},
        {"text": "Assess medication adherence.", "metadata": {}},
    ]

    selected = _deduplicate(chunks)

    assert [chunk["text"] for chunk in selected] == [
        "Check blood pressure and repeat measurement.",
        "Assess medication adherence.",
    ]