"""抓人事行政總處辦公日曆表,輸出 backend/app/data/holidays.json(只留非週末的放假日)。

用法:python preprocessing/fetch_holidays.py
"""
import json
import re
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

_DATASET_API = "https://data.gov.tw/api/v2/rest/dataset/14718"  # 中華民國政府行政機關辦公日曆表
_OUTPUT = Path(__file__).resolve().parent.parent / "backend" / "app" / "data" / "holidays.json"
_CLOSED = "2"  # CSV 的「是否放假」欄:2 = 放假,0 = 上班
_YEAR_IN_NAME = re.compile(r"(\d{3})年")
_TIMEOUT = 30


def _get(url: str) -> bytes:
    # 部分下載連結的 name= 參數直接放中文,未編碼會在送出前就爆 ascii 錯
    safe_url = urllib.parse.quote(url, safe=":/?&=%+")
    request = urllib.request.Request(safe_url, headers={"User-Agent": "appeal-ai/1.0"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read()


def _decode(raw: bytes) -> str:
    """舊年度是 Big5,近年是 UTF-8(含 BOM),兩種都要吃。"""
    for encoding in ("utf-8-sig", "cp950"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _csv_urls() -> dict[int, str]:
    """每個民國年取一個非 Google 版;同年有多筆時取列在最後的,那是修正版。"""
    payload = json.loads(_get(_DATASET_API))
    result = payload["result"]
    urls: dict[int, str] = {}
    for item in result.get("distribution", []):
        name = item.get("resourceDescription", "")
        url = item.get("resourceDownloadUrl", "")
        matched = _YEAR_IN_NAME.search(name)
        if not matched or "Google" in name or not url.lower().startswith("http"):
            continue
        urls[int(matched.group(1))] = url
    return urls


def _closed_weekdays(text: str) -> dict[str, str]:
    """回傳非週末的放假日;週末本來就順延,交給 compute_deadline 判斷,不重複記。"""
    holidays: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        cells = [c.strip() for c in line.split(",")]
        if len(cells) < 3 or len(cells[0]) != 8 or not cells[0].isdigit():
            continue
        if cells[2] != _CLOSED:
            continue
        day = date(int(cells[0][:4]), int(cells[0][4:6]), int(cells[0][6:]))
        if day.weekday() >= 5:
            continue
        holidays[day.isoformat()] = cells[3] if len(cells) > 3 and cells[3] else "調整放假"
    return holidays


def main() -> None:
    holidays: dict[str, str] = {}
    for roc_year, url in sorted(_csv_urls().items()):
        found = _closed_weekdays(_decode(_get(url)))
        holidays.update(found)
        print(f"{roc_year} 年:{len(found)} 天")
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(
        json.dumps(dict(sorted(holidays.items())), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )
    print(f"共 {len(holidays)} 天 -> {_OUTPUT}")


if __name__ == "__main__":
    main()
