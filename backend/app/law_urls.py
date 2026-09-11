"""原文連結:法規條號與司法院釋字 -> 全國法規資料庫網址;對照表由 preprocessing/fetch_law_urls.py 產生。"""
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

_DATA = Path(__file__).resolve().parent / "data" / "law_pcode.json"
_SINGLE = "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode={pcode}&flno={flno}"
_ALL = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}"


@lru_cache(maxsize=1)
def load_pcodes() -> dict[str, str]:
    """檔案不存在就回空表:連結是附加資訊,缺了不該讓整個檢索掛掉。"""
    if not _DATA.exists():
        return {}
    return json.loads(_DATA.read_text(encoding="utf-8"))


def law_article_url(law_name: Optional[str], article_no: Optional[str]) -> Optional[str]:
    """查不到 pcode 就回 None。猜一個 pcode 會連到別部法規,比沒有連結更糟。"""
    pcode = load_pcodes().get((law_name or "").strip())
    if not pcode:
        return None
    flno = (article_no or "").strip()
    return _SINGLE.format(pcode=pcode, flno=flno) if flno else _ALL.format(pcode=pcode)


_INTERPRETATION = "https://law.moj.gov.tw/LawClass/ExContent.aspx?ty=C&CC=D&CNO={number}"
_INTERPRETATION_NO_RE = re.compile(r"釋字第\s*(\d+)\s*號")


def interpretation_url(doc_kind: Optional[str], name: Optional[str]) -> Optional[str]:
    """只有司法院釋字推得出穩定網址(靠號數)。行政函釋各部會來源不一、行政法院裁判要完整
    案號參數,兩類一律回 None——給一個點進去還要再找一次的搜尋頁,不算給了連結。"""
    if (doc_kind or "").strip() != "司法院釋字":
        return None
    match = _INTERPRETATION_NO_RE.search(name or "")
    return _INTERPRETATION.format(number=match.group(1)) if match else None
