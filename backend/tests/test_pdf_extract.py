import fitz

from app.pdf_extract import extract_text


def test_extract_text_from_simple_pdf():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Appellant Wang")
    pdf_bytes = doc.tobytes()
    doc.close()

    text = extract_text(pdf_bytes)
    assert "Appellant" in text
