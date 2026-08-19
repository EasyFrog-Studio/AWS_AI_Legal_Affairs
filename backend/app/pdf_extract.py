"""PDF bytes -> 純文字。"""
import fitz  # PyMuPDF


def extract_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)
