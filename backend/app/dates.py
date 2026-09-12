"""民國日期的唯一標準寫法。

F1 的日期欄由模型抽出,寫法各異(「114.7.4」「中華民國 114 年 7 月 4 日」「114年7月4日10時30分」);
前端的日期選擇器與決定書都只認一種寫法,所以寫入前統一走 normalize_roc。時分捨棄:期間計算
只用到日(deadline.py 全以 date 運算)。
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from app.models import CASE_INFO_DATE_FIELDS, CaseInfo

_ROC_YEAR_OFFSET = 1911
_ERA = r"(?:中華民國|民國)?\s*"
# 「114年7月4日」「114 年 7 月 4 日」
_CJK = re.compile(rf"^{_ERA}(\d{{1,3}})\s*年\s*(\d{{1,2}})\s*月\s*(\d{{1,2}})\s*日")
# 「114.7.4」「114/07/04」「114-7-4」
_SEP = re.compile(rf"^{_ERA}(\d{{1,3}})\s*[./\-]\s*(\d{{1,2}})\s*[./\-]\s*(\d{{1,2}})(?!\d)")
# 「2025-07-04」「2025/7/4」西元,只在年份 ≥ 1912 時視為西元
_ISO = re.compile(r"^(\d{4})\s*[./\-]\s*(\d{1,2})\s*[./\-]\s*(\d{1,2})(?!\d)")


def parse_roc(text: Optional[str]) -> Optional[date]:
    """任一常見寫法 -> date;解析不出或日期不存在(如 2 月 30 日)回 None。"""
    if not text:
        return None
    s = text.strip()
    for pattern, offset in ((_ISO, 0), (_CJK, _ROC_YEAR_OFFSET), (_SEP, _ROC_YEAR_OFFSET)):
        m = pattern.match(s)
        if not m:
            continue
        year = int(m.group(1)) + offset
        if offset == 0 and year < _ROC_YEAR_OFFSET + 1:
            continue
        try:
            return date(year, int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


def normalize_roc(text: Optional[str]) -> Optional[str]:
    """任一常見寫法 -> 「民國114年7月4日」;解析不出回 None。"""
    value = parse_roc(text)
    return format_roc_full(value) if value else None


def format_roc_full(value: date) -> str:
    """date -> 標準寫法「民國114年7月4日」。與 deadline.format_roc 不同,那個是決定書理由內的短寫法。"""
    return f"民國{value.year - _ROC_YEAR_OFFSET}年{value.month}月{value.day}日"


def normalize_case_info_dates(info: CaseInfo) -> CaseInfo:
    """把 CASE_INFO_DATE_FIELDS 全部正規化;解析不出的保留原文,前端據此顯示「無法辨識」。"""
    updates = {}
    for field in CASE_INFO_DATE_FIELDS:
        raw = getattr(info, field)
        normalized = normalize_roc(raw)
        if normalized is not None and normalized != raw:
            updates[field] = normalized
    return info.model_copy(update=updates) if updates else info
