"""決定書草稿 PDF:體例照語料 21 份真實決定書,系統填不出來的欄位留空給承辦人。
逐行手動換行分頁,insert_textbox 遇長文會裁切。"""
from __future__ import annotations

import os
import re

import fitz

from app.config import settings

_BUILTIN_FONT = "china-t"
_EMBEDDED_FONT_NAME = "kai"


def resolve_font() -> tuple[str, str | None]:
    """回傳 (fontname, fontfile);字型檔不存在就退回內建 CJK 字型。"""
    path = settings.DECISION_FONT_FILE
    if path and os.path.isfile(path):
        return (_EMBEDDED_FONT_NAME, path)
    # 內建 china-t 沒有 ToUnicode CMap:畫得出來,但 PDF 的文字複製出來是亂碼
    return (_BUILTIN_FONT, None)
_MARGIN = 60
_PAGE_RECT = fitz.paper_rect("a4")
_PAGE_WIDTH = _PAGE_RECT.width
_PAGE_HEIGHT = _PAGE_RECT.height
_BODY_WIDTH = _PAGE_WIDTH - 2 * _MARGIN

_TITLE_SIZE = 16
_HEADING_SIZE = 13
_BODY_SIZE = 11
_LINE_GAP = 1.6

_AUTHORITY_TITLE = "新北市政府訴願決定書"
_BLANK = "　　　　　　"  # 全形空白,列印後承辦人可直接手寫
_COMMITTEE_LINES = 12  # 語料每案 10~14 位委員,取中位數留行數,名單與人數都不由系統決定
_LITIGATION_NOTICE = (
    "如不服本決定,得於決定書送達之次日起 2 個月內向臺北高等行政法院"
    "(地址:臺北市士林區福國路 101 號)提起行政訴訟。"
)


def _wrap_line(text: str, fontsize: float, max_width: float, measure) -> list[str]:
    """依可用寬度切成多行(逐字元累加寬度量測,中文不分詞)。"""
    lines: list[str] = []
    current = ""
    for ch in text:
        candidate = current + ch
        if current and measure(candidate, fontsize) > max_width:
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
        self.fontname, self.fontfile = resolve_font()
        # 檔案字型的寬度量測不能走 get_text_length(那只認內建字型名)
        self._font = fitz.Font(fontfile=self.fontfile) if self.fontfile else None
        self.page = self._new_page()
        self.y = _MARGIN

    def _new_page(self) -> fitz.Page:
        page = self.doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        if self.fontfile:
            page.insert_font(fontname=self.fontname, fontfile=self.fontfile)
        return page

    def measure(self, text: str, fontsize: float) -> float:
        if self._font is not None:
            return self._font.text_length(text, fontsize)
        return fitz.get_text_length(text, fontname=self.fontname, fontsize=fontsize)

    def _ensure_space(self, needed: float) -> None:
        if self.y + needed > _PAGE_HEIGHT - _MARGIN:
            self.page = self._new_page()
            self.y = _MARGIN

    def write_line(self, text: str, fontsize: float, align: str = "left") -> None:
        line_height = fontsize * _LINE_GAP
        self._ensure_space(line_height)
        if align == "center":
            text_width = self.measure(text, fontsize)
            x = _MARGIN + (_BODY_WIDTH - text_width) / 2
        else:
            x = _MARGIN
        self.page.insert_text((x, self.y + fontsize), text, fontname=self.fontname, fontsize=fontsize)
        self.y += line_height

    def write_paragraph(self, text: str, fontsize: float = _BODY_SIZE) -> None:
        for raw_line in text.splitlines() or [""]:
            if not raw_line:
                self.y += fontsize * _LINE_GAP
                continue
            for line in _wrap_line(raw_line, fontsize, _BODY_WIDTH, self.measure):
                self.write_line(line, fontsize)

    def gap(self, amount: float) -> None:
        self.y += amount


def build_decision_blocks(case) -> list[tuple[str, str]]:
    """決定書草稿的版面區塊 [(kind, text)];kind ∈ title/heading/body/blank。
    體例照語料 21 份真實決定書,系統填不出來的欄位留空給承辦人。
    唯一的消費者是 decision_plain_text 與 docx_render:版面只有這一份定義。"""
    f1 = case.f1
    header = case.decision_header
    blocks: list[tuple[str, str]] = [
        ("title", _AUTHORITY_TITLE),
        ("blank", ""),
        ("body", f"案　　號:{_value(header.case_no)}"),
        ("body", f"　訴願人　{_value(header.appellant or (f1 and f1.appellant))}"),
        ("body", f"　原處分機關　{_value(header.agency or (f1 and f1.agency))}"),
        ("blank", ""),
        ("body", _opening_paragraph(f1)),
        ("blank", ""),
    ]

    f4 = case.f4
    for heading, field, body in (
        ("主　文", "main_text", f4.main_text),
        ("事　實", "fact", f4.fact),
        ("理　由", "reason", f4.reason),
    ):
        # 不受理決定得不記載事實(訴願法§89 I(3)),語料 90 件事實欄全空,不留空標題
        if field == "fact" and f4.draft_type == "不受理" and not (f4.fact or "").strip():
            continue
        blocks.append(("heading", heading))
        blocks.append(("body", body or ""))
        blocks.append(("blank", ""))

    blocks.append(("body", f"訴願審議委員會主任委員　{_value(header.chairman)}"))
    # 填了名單就照名單印,沒填才留 _COMMITTEE_LINES 行空白——人數不由系統決定
    members = [line.strip() for line in header.committee.splitlines() if line.strip()]
    for member in members or [_BLANK] * _COMMITTEE_LINES:
        blocks.append(("body", f"委員　{member}"))
    blocks.append(("blank", ""))
    # 訴願有理由(撤銷/撤銷另處)就沒有要救濟的對象,其餘類型都附教示段
    if f4.draft_type not in ("原處分撤銷", "撤銷另處"):
        blocks.append(("body", _LITIGATION_NOTICE))
        blocks.append(("blank", ""))
    blocks.append(("body", f"中華民國{header.decided_date or '　　　　年　　　月　　　日'}"))
    return blocks


def _value(text) -> str:
    """填不出來就留下可書寫的空白,不印 None,也不留完全沒有記號的空行。"""
    return text if text else _BLANK


_ERA_PREFIX_RE = re.compile(r"^\s*(?:中華)?民國\s*")


def _strip_era(date: str) -> str:
    """抬頭模板自己寫了「民國」,而 F1 的 disposition_date 保留原文寫法、公文書多半自帶紀年,
    不脫掉就會印出「民國民國110年8月31日」。"""
    return _ERA_PREFIX_RE.sub("", date)


def _opening_paragraph(f1) -> str:
    case_type = _value(f1 and f1.case_type)
    date = _strip_era(_value(f1 and f1.disposition_date))
    doc_no = _value(f1 and f1.disposition_no)
    return (
        f"上列訴願人因{case_type}事件,不服原處分機關民國{date}{doc_no}"
        "所為之處分,提起訴願一案,本府依法決定如下:"
    )


def decision_plain_text(case) -> str:
    """把版面攤平成承辦人實際編輯的那一份全文。F4 產出時呼叫一次寫進 draft_plain_text;
    之後這份文字就是決定書本身,f4 三欄只是產生它的素材。"""
    lines = []
    for kind, text in build_decision_blocks(case):
        lines.append("" if kind == "blank" else text)
    return "\n".join(lines)


def _render_plain_pdf(text: str) -> bytes:
    """純文字照打的樣子印,不套任何體例套語。走 write_paragraph 是為了過長的行會折行——
    write_line 不折,超出版心就直接被裁掉。"""
    doc = fitz.open()
    w = _Writer(doc)
    w.write_paragraph(text or "")
    doc.subset_fonts()
    return doc.tobytes()


def render_draft_pdf(case) -> bytes:
    """依 case.draft_plain_text 產生決定書草稿 PDF bytes。
    承辦人編輯與下載的都是那一份全文;f4 只是產生它的素材,不再直接印。"""
    return _render_plain_pdf(case.draft_plain_text)
