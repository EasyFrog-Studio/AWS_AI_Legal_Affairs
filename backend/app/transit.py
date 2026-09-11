"""在途期間對照表:訴願扣除在途期間辦法附表,由 preprocessing/fetch_transit_table.py 產生。"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

_DATA = Path(__file__).resolve().parent / "data" / "transit_days.json"


@lru_cache(maxsize=1)
def _table() -> dict:
    if not _DATA.exists():
        return {"table": {}, "districts": {}}
    return json.loads(_DATA.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _split_cities() -> tuple[str, ...]:
    """附表把部分直轄市依行政區拆成(一)(二);哪幾個由資料決定,不在程式裡另抄一份。"""
    return tuple({key.split("（")[0] for key in _table()["districts"]})


def table_source() -> str:
    """附表的抓取來源網址,供 /api/health 回報這份對照資料出自哪裡。"""
    return _table().get("source", "")


def agency_count() -> int:
    """收錄的受理機關所在地列數;0 代表這份對照資料根本沒載入。"""
    return len(_table()["table"])


def _normalize(text: str) -> str:
    """卷內「台中市」與法規「臺中市」混用,一律正規化為法規用字。"""
    return text.replace("台", "臺")


def residence_key(text: str) -> Optional[str]:
    """從住居所文字判斷附表的住居地欄名;判不出來回 None,不猜。"""
    normalized = _normalize(text)
    districts = _table()["districts"]
    for city in _split_cities():
        if city not in normalized:
            continue
        for key, names in districts.items():
            if key.startswith(city) and any(name in normalized for name in names):
                return key
        return None  # 是這三市,但行政區不明(常見於遮罩地址),日數無從判斷
    candidates = [
        key
        for key in _table()["table"]
        if not key.startswith(_split_cities()) and key in normalized
    ]
    return candidates[0] if len(candidates) == 1 else None


class TransitLookup(BaseModel):
    """查表結果。days 為 None 代表無從認定;review_note 只在「同一直轄市各組日數不同」
    這一種情形有值——那是唯一「查得到資料但仍不能用」的情形,承辦人需要知道差幾日才知道
    自行認定行政區值不值得。純粹查不到(住居所不在附表內)不編造說明。"""

    days: Optional[int] = None
    review_note: str = ""


def _masked_city(residence_text: str) -> Optional[str]:
    """住居所寫得出直轄市但行政區不明(遮罩地址)時回該市名,否則 None。"""
    normalized = _normalize(residence_text)
    for city in _split_cities():
        if city in normalized:
            return city
    return None


def _group_days(agency_location: str, city: str) -> dict[str, int]:
    """該受理機關對某直轄市各分組((一)(二))的日數。欄名以 districts 為準,不能只看前綴——
    「高雄市政府代管東沙島、太平島」也以高雄市起頭,但它是獨立的住居地欄,不是高雄市的分組。"""
    row = _table()["table"].get(_normalize(agency_location), {})
    return {key: row[key] for key in _table()["districts"] if key.startswith(city) and key in row}


def resolve_transit_days(residence_text: str, agency_location: str) -> TransitLookup:
    """住居所文字 + 受理訴願機關所在地 -> 在途期間日數。

    行政區不明(遮罩地址如「臺中市○○區」)時不必整筆棄權:附表把部分直轄市依行政區拆成
    (一)(二),但那兩組對特定受理機關的日數常常相同(受理機關為新北市時,臺中/臺南/高雄三市
    的兩組全部相同),此時(一)(二)之分一天都不差,照該值計算即可。兩組不同才回 None,
    並說明是哪個城市差幾日——不得以「取較長日數對人民有利」補值,那是法律推論,
    在途期間辦法附表沒有這條規則。
    """
    key = residence_key(residence_text)
    if key is not None:
        return TransitLookup(days=_table()["table"].get(_normalize(agency_location), {}).get(key))

    city = _masked_city(residence_text)
    if city is None:
        return TransitLookup()

    groups = _group_days(agency_location, city)
    values = set(groups.values())
    if len(values) == 1:
        return TransitLookup(days=values.pop())
    if not values:
        return TransitLookup()

    detail = "、".join(f"{key}{days}日" for key, days in sorted(groups.items()))
    return TransitLookup(
        review_note=(
            f"住居所行政區不明，{city}附表分組日數不同（{detail}，相差{max(values) - min(values)}日），"
            "在途期間無從認定"
        )
    )


def lookup_transit_days(residence_text: str, agency_location: str) -> Optional[int]:
    """住居所文字 + 受理訴願機關所在地 -> 在途期間日數;任一端查不到就回 None。"""
    return resolve_transit_days(residence_text, agency_location).days
