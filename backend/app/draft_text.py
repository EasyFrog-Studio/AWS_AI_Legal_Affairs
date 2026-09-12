"""決定書本文(draft_plain_text)的邊界。

本文只含「主　文 / 事　實 / 理　由」三段;表頭與結尾是 DecisionHeader 的結構化欄位。
表頭改結構化之前存的全文含標題列與委員署名,讀出時剝成本文,兩種格式的案件才能走同一條路。
"""
from __future__ import annotations

from app.models import DRAFT_SECTIONS

_LEGACY_TITLE = "新北市政府訴願決定書"
_TAIL_MARKER = "訴願審議委員會主任委員"
_HEADINGS = tuple(heading for heading, _ in DRAFT_SECTIONS)


def is_legacy_full_text(text: str) -> bool:
    lines = text.lstrip().splitlines()
    return bool(lines) and (lines[0].startswith(_LEGACY_TITLE) or any(line.startswith(_TAIL_MARKER) for line in lines))


def body_from_legacy_text(text: str) -> str:
    """含表頭與結尾的舊全文 -> 只剩三段本文;已是本文的原樣回傳。找不到任何段標題時原樣回傳,不丟內容。"""
    if not text or not is_legacy_full_text(text):
        return text
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() in _HEADINGS), None)
    if start is None:
        return text
    end = next((i for i, line in enumerate(lines) if line.startswith(_TAIL_MARKER)), len(lines))
    return "\n".join(lines[start:end]).strip()
