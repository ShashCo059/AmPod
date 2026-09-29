from pathlib import Path

from pypdf import PdfReader


def load_pdf_pages(pdf_path):
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    reader = PdfReader(str(pdf_path))
    pages = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        pages.append({
            "text": text,
            "page": page_number,
            "ocr_required": len(text) < 40,
        })
    return pages


def load_pdf(pdf_path):
    """Return extracted PDF text for compatibility with older callers."""
    return "\n".join(page["text"] for page in load_pdf_pages(pdf_path) if page["text"])


def load_all_pdfs(folder_path):
    folder_path = Path(folder_path)
    if not folder_path.exists():
        return {}

    pdf_docs = {}
    for pdf_file in sorted(folder_path.glob("*.pdf")):
        pdf_docs[pdf_file.name] = load_pdf(pdf_file)
    return pdf_docs