from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_pages(pages, source):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = []
    for page in pages:
        if not page["text"]:
            continue
        page_chunks = splitter.split_text(page["text"])
        for chunk_number, text in enumerate(page_chunks, start=1):
            chunks.append({
                "text": text,
                "metadata": {
                    "source": source,
                    "page": page["page"],
                    "chunk": chunk_number,
                    "ocr_required": page["ocr_required"],
                },
            })
    return chunks


def chunk_text(text):
    """Return text-only chunks for compatibility with older callers."""

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )

    chunks = splitter.split_text(text)

    return chunks


def chunk_word_blocks(blocks, source):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1200,
        chunk_overlap=150,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    chunks = []
    for block in blocks:
        for chunk_number, text in enumerate(splitter.split_text(block["text"]), start=1):
            chunks.append({
                "text": text,
                "metadata": {
                    "source": source,
                    "block": str(block["block"]),
                    "chunk": chunk_number,
                    "heading": block.get("heading", ""),
                },
            })
    return chunks