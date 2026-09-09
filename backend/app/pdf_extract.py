"""PDF bytes -> 純文字,並回報文字量與可讀比例(判斷是否為掃描件)。"""
import fitz  # PyMuPDF
from pydantic import BaseModel

from app.text_quality import readable_char_ratio


class PdfText(BaseModel):
    """char_count 不計空白:表單式 PDF 的文字層常常只有一堆換行與全形空白,
    含空白算會讓「其實沒有文字層」看起來像有 300 字。"""

    text: str
    char_count: int
    readable_ratio: float


def extract_text(pdf_bytes: bytes) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def extract_text_quality(pdf_bytes: bytes) -> PdfText:
    """抽字並一併量文字品質。掃描件的判準(字數)與亂碼的判準(可讀比例)是兩件事,
    兩個數字都給呼叫端,不在這裡替它決定。"""
    text = extract_text(pdf_bytes)
    stripped = "".join(text.split())
    return PdfText(text=text, char_count=len(stripped), readable_ratio=readable_char_ratio(stripped))
