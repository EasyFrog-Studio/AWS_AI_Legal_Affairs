"""決定書草稿 PDF:體例照語料 21 份真實決定書,系統填不出來的欄位留空給承辦人。
逐行手動換行分頁,insert_textbox 遇長文會裁切。"""
from __future__ import annotations

import os
import re

import fitz

from app.config import settings
from app.models import DRAFT_SECTIONS

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
_ISSUED_NO_PLACEHOLDER = "新北府訴決字第　　　　號"  # 發文字號發文時才由案管系統配,套語留空號
_COMMITTEE_LINES = 12  # 語料每案 10~14 位委員,取中位數留行數,名單與人數都不由系統決定
_LITIGATION_NOTICE = (
    "如不服本決定，得於決定書送達之次日起 2 個月內向臺北高等行政法院"
    "（地址：臺北市士林區福國路 101 號）提起行政訴訟。"
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


def decision_body_text(case) -> str:
    """決定書本文:只有主文/事實/理由三段,這就是 draft_plain_text 的內容。
    唯一的消費者是 pipeline(F4 落地時寫入 draft_plain_text)與 models 的舊全文補值。"""
    f4 = case.f4
    parts = []
    for heading, field in DRAFT_SECTIONS:
        content = getattr(f4, field) or ""
        # 不受理決定得不記載事實(訴願法§89 I(3)),語料 90 件事實欄全空,不留空標題
        if field == "fact" and f4.draft_type == "不受理" and not content.strip():
            continue
        parts.append(f"{heading}\n{content}")
    return "\n\n".join(parts)


# 舊名保留:pipeline 與 models.py 的舊全文補值都呼叫這個名字
decision_plain_text = decision_body_text


def build_decision_blocks(case) -> list[tuple[str, str]]:
    """決定書草稿的完整版面區塊 [(kind, text)];kind ∈ title/heading/body/blank。
    欄位序:結構化表頭五欄(案號/要旨/發文日期/發文字號/相關法條)→標題+案號→訴願人→
    代理人(有才印)→原處分機關→敘明句→本文(case.draft_plain_text 原樣)→主任委員→
    委員→教示條款→日期。體例照語料 21 份真實決定書,系統填不出來的欄位留空給承辦人。
    唯一的消費者是 decision_full_text 與 docx_render:版面只有這一份定義。"""
    f1 = case.f1
    f4 = case.f4
    header = case.decision_header

    blocks: list[tuple[str, str]] = [
        ("body", f"案　　號：{_value(header.case_no)}"),
        ("body", f"要　　旨：{_value(header.gist)}"),
        ("body", f"發文日期：{_value(_strip_era(header.issued_date)) if header.issued_date else _BLANK}"),
        ("body", f"發文字號：{header.issued_no or _ISSUED_NO_PLACEHOLDER}"),
    ]
    law_lines = [line for line in header.related_laws.splitlines() if line.strip()]
    if law_lines:
        blocks.append(("body", f"相關法條：{law_lines[0]}"))
        for line in law_lines[1:]:
            blocks.append(("body", line))
    else:
        blocks.append(("body", f"相關法條：{_BLANK}"))
    blocks.append(("blank", ""))

    case_no_suffix = f"　　案號：{header.case_no} 號" if header.case_no else ""
    blocks.append(("title", f"{_AUTHORITY_TITLE}{case_no_suffix}"))
    blocks.append(("body", f"　訴願人　{_value(header.appellant or (f1 and f1.appellant))}"))
    if header.agent_name:
        agent_label = header.agent_role or "代理人"
        blocks.append(("body", f"　{agent_label}　{header.agent_name}"))
    blocks.append(("body", f"　原處分機關　{_value(header.agency or (f1 and f1.agency))}"))
    blocks.append(("blank", ""))
    blocks.append(("body", _opening_paragraph(f1)))
    blocks.append(("blank", ""))

    blocks.append(("body", case.draft_plain_text or ""))
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
        f"上列訴願人因{case_type}事件，不服原處分機關民國{date}{doc_no}"
        "所為之處分，提起訴願一案，本府依法決定如下："
    )


def decision_full_text(case) -> str:
    """把整份版面(表頭+本文+結尾)攤平成 PDF/Word 實際印出的那一份全文。
    本文段落取 case.draft_plain_text 原樣——承辦人改過的就是它,不再從 f4 三段重組。"""
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
    """依 build_decision_blocks 產生決定書草稿 PDF bytes:結構化表頭+本文+結尾整份都印,
    承辦人下載的是完整可送出的決定書,不是只有本文那一段。"""
    return _render_plain_pdf(decision_full_text(case))
