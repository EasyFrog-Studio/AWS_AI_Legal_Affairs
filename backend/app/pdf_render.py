"""決定書草稿 PDF 產生(見 DECISIONS.md「Backend API 契約」節:GET .../draft.pdf)。

只含決定書本文:標題 + 主文 + 事實欄 + 理由欄,不含依據(cited_laws)。
中文以 fitz 內建 CJK 字型 china-t 繪製;逐行手動換行與分頁,避免
insert_textbox 遇長文時裁切遺失內容。
"""
from __future__ import annotations

import fitz

_FONT = "china-t"
_MARGIN = 60
_PAGE_RECT = fitz.paper_rect("a4")
_PAGE_WIDTH = _PAGE_RECT.width
_PAGE_HEIGHT = _PAGE_RECT.height
_BODY_WIDTH = _PAGE_WIDTH - 2 * _MARGIN

_TITLE_SIZE = 16
_HEADING_SIZE = 13
_BODY_SIZE = 11
_LINE_GAP = 1.6


def _wrap_line(text: str, fontsize: float, max_width: float) -> list[str]:
    """依可用寬度切成多行(逐字元累加寬度量測,中文不分詞)。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if current and fitz.get_text_length(candidate, fontname=_FONT, fontsize=fontsize) > max_width:
            lines.append(current)
            current = ch
        else:
            current = candidate
    lines.append(current)
    return lines


class _Writer:
    """追蹤目前頁與 y 游標,超出頁面時自動換頁。"""

    def __init__(self, doc: fitz.Document) -> None:
        self.doc = doc
        self.page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        self.y = _MARGIN

    def _ensure_space(self, needed: float) -> None:
        if self.y + needed > _PAGE_HEIGHT - _MARGIN:
            self.page = self.doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
            self.y = _MARGIN

    def write_line(self, text: str, fontsize: float, align: str = "left") -> None:
        line_height = fontsize * _LINE_GAP
        self._ensure_space(line_height)
        if align == "center":
            text_width = fitz.get_text_length(text, fontname=_FONT, fontsize=fontsize)
            x = _MARGIN + (_BODY_WIDTH - text_width) / 2
        else:
            x = _MARGIN
        self.page.insert_text((x, self.y + fontsize), text, fontname=_FONT, fontsize=fontsize)
        self.y += line_height

    def write_paragraph(self, text: str, fontsize: float = _BODY_SIZE) -> None:
        for raw_line in text.splitlines() or [""]:
            if not raw_line:
                self.y += fontsize * _LINE_GAP
                continue
            for line in _wrap_line(raw_line, fontsize, _BODY_WIDTH):
                self.write_line(line, fontsize)

    def gap(self, amount: float) -> None:
        self.y += amount


def render_draft_pdf(case) -> bytes:
    """依 case.f4 產生決定書草稿 PDF bytes(case.f4 必須非 None)。"""
    f4 = case.f4
    doc = fitz.open()
    w = _Writer(doc)

    w.write_line("新北市政府訴願決定書(草稿)", _TITLE_SIZE, align="center")
    w.gap(8)
    w.write_line(f"案號　{case.case_id}", _BODY_SIZE, align="center")
    w.gap(20)

    sections = [("主　文", f4.main_text), ("事　實", f4.fact), ("理　由", f4.reason)]
    for heading, body in sections:
        w.write_line(heading, _HEADING_SIZE)
        w.gap(6)
        w.write_paragraph(body or "")
        w.gap(16)

    return doc.tobytes()
