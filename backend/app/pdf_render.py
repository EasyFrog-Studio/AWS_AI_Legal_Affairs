"""決定書草稿 PDF:字體為明體(新細明體),體例照語料 21 份真實決定書,系統填不出來的欄位留空給承辦人。
逐行手動換行分頁,insert_textbox 遇長文會裁切。"""
from __future__ import annotations

import os
import re

import fitz

from app.config import settings
from app.models import DRAFT_SECTIONS

_BUILTIN_FONT = "china-t"
_EMBEDDED_FONT_NAME = "ming"


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

_BODY_SIZE = 11
_LINE_GAP = 1.6

_AUTHORITY_TITLE = "新北市政府訴願決定書"
_META_LABELS = {"case_no": "案　　號", "gist": "要　　旨"}  # 兩字欄名撐成四字寬,與發文日期對齊
_FULL_TEXT_LABEL = "全　　文："
_META_GAP = "　"  # 欄名與值之間退一格,值在同一欄起筆
_LAW_INDENT = "　　　　　　"  # 相關法條續行對齊到值欄:欄名四字 + 冒號 + 間隔
_PARTY_INDENT = "    "  # 抬頭當事人列的縮排
_ITEM_HANGING = "    "  # 條列「一、」的續行退回條號之後
_GAP = "  "  # 標籤與值之間的間隔,語料一律兩個半形空格
# 承辦人編輯的段落標題 -> 列印體例
_HEADINGS = {h: "    " + "    ".join(h.split("　")) for h, _field in DRAFT_SECTIONS}
SPLIT = "\t"  # split 區塊的左右分隔,由各 render 自行靠邊
NUMBERED_ITEM = re.compile(r"^[一二三四五六七八九十]+、")
_LITIGATION_NOTICE = (
    "如不服本決定，得於決定書送達之次日起 2 個月內向臺北高等行政法院"
    "（地址：臺北市士林區福國路 101 號）提起行政訴訟。"
)


def _wrap_line(text: str, fontsize: float, max_width: float, measure, hanging: str = "") -> list[str]:
    """依可用寬度切成多行(逐字元累加寬度量測,中文不分詞);hanging 是續行的縮排。"""
    lines: list[str] = []
    current = ""
    hang_width = measure(hanging, fontsize) if hanging else 0
    for ch in text:
        candidate = current + ch
        limit = max_width - (hang_width if lines else 0)
        if current and measure(candidate, fontsize) > limit:
            lines.append(current)
            current = ch
        else:
            current = candidate
    lines.append(current)
    return lines[:1] + [f"{hanging}{line}" for line in lines[1:]]


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

    def write_line(self, text: str, fontsize: float) -> None:
        line_height = fontsize * _LINE_GAP
        self._ensure_space(line_height)
        x = _MARGIN
        self.page.insert_text((x, self.y + fontsize), text, fontname=self.fontname, fontsize=fontsize)
        self.y += line_height

    def write_split_line(self, left: str, right: str, fontsize: float = _BODY_SIZE) -> None:
        """一列兩端:左靠版心左界、右靠右界。抬頭的機關名與案號就是這樣排的。"""
        line_height = fontsize * _LINE_GAP
        self._ensure_space(line_height)
        baseline = self.y + fontsize
        self.page.insert_text((_MARGIN, baseline), left, fontname=self.fontname, fontsize=fontsize)
        if right:
            x = _MARGIN + _BODY_WIDTH - self.measure(right, fontsize)
            self.page.insert_text((x, baseline), right, fontname=self.fontname, fontsize=fontsize)
        self.y += line_height

    def write_paragraph(self, text: str, fontsize: float = _BODY_SIZE) -> None:
        for raw_line in text.splitlines() or [""]:
            if not raw_line:
                self.y += fontsize * _LINE_GAP
                continue
            hanging = _ITEM_HANGING if NUMBERED_ITEM.match(raw_line) else ""
            for line in _wrap_line(raw_line, fontsize, _BODY_WIDTH, self.measure, hanging):
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
    """決定書草稿的完整版面區塊 [(kind, text)];kind ∈ split/body/blank。
    欄位序與逐字體例照語料 17.114年-違反廢棄物清理法事件-79I:表頭五欄(標籤一律四字寬)→
    全文標籤→抬頭(機關名靠左、案號靠右,故為 split)→當事人(縮排四格)→敘明句→本文→
    主任委員→委員→教示條款→日期。系統填不出來的欄位留空給承辦人。
    唯一的消費者是 decision_full_text 與兩支 render:版面只有這一份定義。"""
    f1 = case.f1
    f4 = case.f4
    header = case.decision_header

    blocks: list[tuple[str, str]] = [
        ("body", _meta_line(_META_LABELS["case_no"], header.case_no)),
        ("body", _meta_line(_META_LABELS["gist"], header.gist)),
        ("body", _meta_line("發文日期", header.issued_date)),
        ("body", _meta_line("發文字號", header.issued_no)),
    ]
    law_lines = [line for line in header.related_laws.splitlines() if line.strip()]
    blocks.append(("body", _meta_line("相關法條", law_lines[0] if law_lines else "")))
    for line in law_lines[1:]:
        blocks.append(("body", f"{_LAW_INDENT}{line}"))
    blocks.append(("body", _FULL_TEXT_LABEL))
    blocks.append(("blank", ""))

    masthead = _AUTHORITY_TITLE
    if header.case_no:
        masthead = f"{masthead}{SPLIT}案號：{header.case_no}{_GAP}號"
    blocks.append(("split", masthead))
    blocks.append(("body", _party_line("訴願人", header.appellant)))
    blocks.append(("body", _party_line(header.agent_role or "代理人", header.agent_name)))
    blocks.append(("body", _party_line("原處分機關", header.agency)))
    blocks.append(("body", header.preamble))

    blocks.append(("body", _printable_body(case.draft_plain_text or "")))

    blocks.append(("body", f"訴願審議委員會主任委員{_GAP}{header.chairman}"))
    # 名單有幾位就印幾列,沒填印一列空的——人數與名單都不由系統決定
    members = [line.strip() for line in header.committee.splitlines() if line.strip()]
    for member in members or [""]:
        blocks.append(("body", f"委員{_GAP}{member}"))
    # 訴願有理由(撤銷/撤銷另處)就沒有要救濟的對象,其餘類型都附教示段
    if f4.draft_type not in ("原處分撤銷", "撤銷另處"):
        blocks.append(("body", _LITIGATION_NOTICE))
    blocks.append(("body", f"中華民國{_META_GAP}{_strip_era(header.decided_date)}"))
    return blocks


def _meta_line(label: str, value) -> str:
    """表頭欄:欄名四字寬,值退一格自成一欄,與語料的欄位表對齊。"""
    return f"{label}：{_META_GAP}{value}"


def _party_line(label: str, name) -> str:
    return f"{_PARTY_INDENT}{label}{_GAP}{name}"


def _printable_body(text: str) -> str:
    """把承辦人編輯的本文排成列印體例:段落標題縮排並拉開字距,其餘逐字不動。"""
    lines = []
    for line in text.splitlines():
        heading = _HEADINGS.get(line.strip())
        lines.append(heading if heading else line)
    return "\n".join(lines)


_ERA_PREFIX_RE = re.compile(r"^\s*(?:中華)?民國\s*")


def _strip_era(date: str) -> str:
    """抬頭模板自己寫了「民國」,而 F1 的 disposition_date 保留原文寫法、公文書多半自帶紀年,
    不脫掉就會印出「民國民國110年8月31日」。"""
    return _ERA_PREFIX_RE.sub("", date)


def decision_full_text(case) -> str:
    """整份版面攤成純文字,供欄位序比對;靠右與懸掛縮排是版面指令,攤平後看不出來,
    實際列印走 render_draft_pdf / render_draft_docx。"""
    lines = []
    for kind, text in build_decision_blocks(case):
        # 靠右是版面指令,攤成純文字時換回看得見的間隔
        lines.append("" if kind == "blank" else text.replace(SPLIT, "　　"))
    return "\n".join(lines)


def render_draft_pdf(case) -> bytes:
    """依 build_decision_blocks 產生決定書草稿 PDF bytes:結構化表頭+本文+結尾整份都印,
    承辦人下載的是完整可送出的決定書,不是只有本文那一段。"""
    doc = fitz.open()
    w = _Writer(doc)
    for kind, text in build_decision_blocks(case):
        if kind == "blank":
            w.gap(_BODY_SIZE * _LINE_GAP)
        elif kind == "split":
            left, _, right = text.partition(SPLIT)
            w.write_split_line(left, right)
        else:
            w.write_paragraph(text)
    doc.subset_fonts()
    return doc.tobytes()
