"""決定書草稿的 Word 輸出:逐行印 draft_plain_text,與 PDF 印的是同一份全文。"""
import io

from docx import Document
from docx.shared import Pt

_FONT = "標楷體"  # 公文書體例;字型不在閱讀端時由 Word 自行回退
_BODY_SIZE = Pt(11)


def _write(doc: Document, text: str, size: Pt):
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.name = _FONT
    run.font.size = size
    return paragraph


def render_draft_docx(case) -> bytes:
    """依 case.draft_plain_text 產出 .docx bytes。與 render_draft_pdf 讀同一個欄位——
    兩邊各自決定內容的下場是同一件案子下載到兩份不一樣的決定書。"""
    doc = Document()
    for line in (case.draft_plain_text or "").splitlines() or [""]:
        _write(doc, line, _BODY_SIZE)
    buffer = io.BytesIO()
    doc.save(buffer)
    return buffer.getvalue()
