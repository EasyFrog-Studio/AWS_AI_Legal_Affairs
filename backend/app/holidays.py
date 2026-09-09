"""國定假日表:人事行政總處辦公日曆表的非週末放假日,由 preprocessing/fetch_holidays.py 產生。"""
import json
from datetime import date
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data" / "holidays.json"


@lru_cache(maxsize=1)
def load_holidays() -> frozenset[date]:
    """檔案不存在就回空集合;呼叫端須以 covered_years 判斷該年是否真的有資料。"""
    if not _DATA.exists():
        return frozenset()
    return frozenset(date.fromisoformat(key) for key in json.loads(_DATA.read_text(encoding="utf-8")))


@lru_cache(maxsize=1)
def covered_years() -> frozenset[int]:
    """有收錄的年度。沒收錄的年度不能當成「該年無假日」,那是安靜算錯。"""
    return frozenset(day.year for day in load_holidays())
