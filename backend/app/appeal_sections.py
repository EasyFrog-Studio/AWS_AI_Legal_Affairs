"""訴願書的「事實」「理由」兩段:照文件自己的標題逐條切開。

為什麼不交給模型:模型分段不穩定,常把論點歸到爭點、事實塞滿而理由留空,
schema required、欄位順序、標題提示等 prompt 寫法都無法可靠阻止。而訴願法§56 I⑤ 要求的
兩段在訴願書格式上本來就各有標題,規則切得準、而且是逐字照抄——法律文書不該被模型改寫。
"""
import re
from typing import Optional

_FACTS_HEADING = re.compile(r"^[　\s]*事[　\s]*實[　\s]*$", re.MULTILINE)
_REASONS_HEADING = re.compile(r"^[　\s]*理[　\s]*由[　\s]*$", re.MULTILINE)
# 理由之後常接的段落:吃進來會讓最後一條理由多出整張附件表格
_TAIL_HEADING = re.compile(
    r"^[　\s]*(?:附[　\s]*送[　\s]*證[　\s]*件|證[　\s]*物|此[　\s]*致|收受或知悉行政處分日期)", re.MULTILINE
)
# 條列標記:行首的「一、」「二、」…;條文內的「第27 條第1 款」不在行首,不會誤切
_ITEM = re.compile(r"^[　\s]*([一二三四五六七八九十]+)、", re.MULTILINE)
# 答辯書也有同樣兩個標題,但那是機關的說法;呼叫端只餵訴願書槽,這裡再擋一次
_ANSWER_MARKER = re.compile(r"訴[　\s]*願[　\s]*答[　\s]*辯[　\s]*書|答[　\s]*辯[　\s]*聲[　\s]*明")


def split_appeal_sections(text: Optional[str]) -> tuple[list[str], list[str]]:
    """訴願書全文 -> (事實條列, 理由條列)。任一段切不出來就回 ([], []):
    切錯比不切更糟——會拿一個壞結果覆蓋掉模型原本抽對的欄位。"""
    text = text or ""
    if _ANSWER_MARKER.search(text):
        return ([], [])
    facts_at = _FACTS_HEADING.search(text)
    reasons_at = _REASONS_HEADING.search(text)
    if facts_at is None or reasons_at is None or facts_at.end() >= reasons_at.start():
        return ([], [])

    tail = _TAIL_HEADING.search(text, reasons_at.end())
    facts = _items(text[facts_at.end() : reasons_at.start()])
    reasons = _items(text[reasons_at.end() : tail.start() if tail else len(text)])
    return (facts, reasons) if facts and reasons else ([], [])


def _items(block: str) -> list[str]:
    """一段文字 -> 依行首「一、」切成逐條;條號下的續行與縮排段落併回該條。"""
    marks = list(_ITEM.finditer(block))
    items = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(block)
        body = block[mark.end() : end]
        # 跨行的內容接回來:PDF 逐行抽字,一條會被硬換成好幾行
        joined = "".join(line.strip() for line in body.splitlines())
        if joined:
            items.append(joined)
    return items
