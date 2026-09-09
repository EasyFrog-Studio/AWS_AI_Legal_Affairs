"""抓訴願扣除在途期間辦法附表,輸出 backend/app/data/transit_days.json。

用法:python preprocessing/fetch_transit_table.py
"""
import json
import re
import urllib.request
from pathlib import Path

_LAW_URL = "https://law.moj.gov.tw/LawClass/LawAll.aspx?PCode=A0030119"  # 訴願扣除在途期間辦法
_OUTPUT = Path(__file__).resolve().parent.parent / "backend" / "app" / "data" / "transit_days.json"
_TIMEOUT = 30
_DIGITS = {"○": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_TENS = {"十": 10, "二十": 20, "三十": 30, "四十": 40}
_NOTE_ITEM = re.compile(r"(臺中市|臺南市|高雄市)（([一二])）指行政區域：([^\d]+?)。")


def _page_text() -> str:
    request = urllib.request.Request(_LAW_URL, headers={"User-Agent": "appeal-ai/1.0"})
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        html = response.read().decode("utf-8")
    text = re.sub(r"<script.*?</script>", "", html, flags=re.S)
    return re.sub(r"<[^>]+>", "\n", text)


def _to_days(cell: str) -> int:
    """「二十日」「○日」等中文日數轉整數;個位在十位之後(如二十五)一併加總。"""
    body = cell.replace("日", "").strip()
    head, _, tail = body.partition("十")
    if not _:
        return _DIGITS[body[0]]
    tens = _TENS["十" if not head else head + "十"]
    return tens + (_DIGITS[tail[0]] if tail else 0)


def _blocks(lines: list[str]) -> list[list[str]]:
    """附表被切成數個橫向區塊,每塊重複一次列名(訴願機關所在地),欄名才是住居地。"""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.startswith("┌"):
            current = []
        elif line.startswith("└"):
            if current:
                blocks.append(current)
            current = []
        elif current is not None:
            current.append(line)
    return [b for b in blocks if any("訴住" in line for line in b)]


def _header_depth(block: list[str]) -> int:
    """表頭最後一行是列標題「訴願機關所在地」的末字,其後才是資料列。"""
    return next(i for i, line in enumerate(block) if "關" in line.split("│")[1]) + 1


def _columns(header: list[str]) -> list[str]:
    """欄名直書,逐字接起來;一格內並排兩段直書(如「高雄市政府代管／東沙島、太平島」)要先讀完左行再讀右行。"""
    width = max(len(line.split("│")) for line in header)
    names = []
    for index in range(2, width - 1):
        cells = [line.split("│")[index] for line in header if len(line.split("│")) > index]
        depth = max(len(cell) for cell in cells)
        name = "".join(cell[i] for i in range(depth) for cell in cells if len(cell) > i)
        names.append(name.replace(" ", "").replace("︵", "（").replace("︶", "）"))
    return names


def _rows(block: list[str], columns: list[str]) -> dict[str, dict[str, int]]:
    table: dict[str, dict[str, int]] = {}
    label = ""
    cells: list[str] = []
    for line in block[_header_depth(block) :]:
        if line.startswith("├"):
            continue
        parts = line.split("│")
        first = parts[1].strip()
        if first:  # 一列的第一行帶列名,其餘兩行只有日數的後續字
            if label:
                table[label] = {c: _to_days(v) for c, v in zip(columns, cells)}
            label, cells = first, [p.strip() for p in parts[2:-1]]
        else:
            cells = [c + p.strip() for c, p in zip(cells, parts[2:-1])]
    if label:
        table[label] = {c: _to_days(v) for c, v in zip(columns, cells)}
    return table


def _districts(text: str) -> dict[str, list[str]]:
    """備註把臺中／臺南／高雄拆成(一)(二),沒有這份對照就無法由行政區反查列名。"""
    # 備註每行前兩格是「備」「註」的直書欄位,要整格丟掉;只去符號會把「註」黏進區名
    body = "".join(line.split("│")[2] for line in text.splitlines() if line.count("│") >= 3)
    flat = re.sub(r"[─\s]", "", body)
    result = {}
    for city, part, districts in _NOTE_ITEM.findall(flat):
        # 法規原文有一處用全形逗號當頓號,兩種都要當分隔
        names = re.split(r"[、，]", districts)
        result[f"{city}（{part}）"] = [d for d in names if d.endswith("區")]
    return result


def main() -> None:
    lines = [line.rstrip() for line in _page_text().splitlines() if line.strip()]
    table: dict[str, dict[str, int]] = {}
    for block in _blocks(lines):
        columns = _columns(block[: _header_depth(block)])
        for agency, row in _rows(block, columns).items():
            table.setdefault(agency, {}).update(row)
    payload = {
        "source": _LAW_URL,
        "table": table,  # {訴願機關所在地: {訴願人住居地: 在途期間日數}}
        "districts": _districts("\n".join(lines)),
    }
    _OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    _OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{len(table)} 個機關所在地 x {len(next(iter(table.values())))} 個住居地 -> {_OUTPUT}")


if __name__ == "__main__":
    main()
