"""全國法規資料庫 Open API -> 法規名稱對 PCode 對照表(backend/app/data/law_pcode.json)。

地端一次性,與 fetch_holidays.py 同一種用法:`python fetch_law_urls.py`。
只收語料實際用到的法規名稱(讀 data/output/law_chunks.jsonl),不是把全庫 11,796 部都寫進 image。
兩支端點都要抓:法律在 ChLaw、命令(辦法/規則/準則)在 ChOrder,少抓一支就有六部辦法查不到。
"""
import io
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_CHUNKS = _ROOT.parent / "data" / "output" / "law_chunks.jsonl"
_OUTPUT = _ROOT / "backend" / "app" / "data" / "law_pcode.json"
_ENDPOINTS = (
    "https://law.moj.gov.tw/api/Ch/Law/JSON",
    "https://law.moj.gov.tw/api/Ch/Order/JSON",
)
_PCODE_RE = re.compile(r"pcode=([A-Z0-9]+)", re.IGNORECASE)


def _fetch_index() -> dict[str, str]:
    """兩支端點合併成 {法規名稱: PCode}。回應是 zip 包住的 JSON,不是裸 JSON。"""
    index: dict[str, str] = {}
    for url in _ENDPOINTS:
        with urllib.request.urlopen(url, timeout=180) as resp:
            payload = resp.read()
        archive = zipfile.ZipFile(io.BytesIO(payload))
        name = next(n for n in archive.namelist() if n.endswith(".json"))
        data = json.loads(archive.read(name).decode("utf-8-sig"))
        laws = data["Laws"] if isinstance(data, dict) else data
        for law in laws:
            match = _PCODE_RE.search(law.get("LawURL") or "")
            if match:
                index.setdefault((law.get("LawName") or "").strip(), match.group(1))
        print(f"{url}:{len(laws)} 部")
    return index


def _corpus_law_names() -> list[str]:
    names = set()
    with _CHUNKS.open(encoding="utf-8") as f:
        for line in f:
            metadata = json.loads(line).get("metadata", {})
            if metadata.get("doc_kind") == "法規" and metadata.get("law_name"):
                names.add(metadata["law_name"].strip())
    return sorted(names)


def main() -> None:
    if not _CHUNKS.exists():
        sys.exit(f"找不到 {_CHUNKS},請先跑 parse_laws.py")
    index = _fetch_index()
    names = _corpus_law_names()
    mapping = {name: index[name] for name in names if name in index}
    missing = [name for name in names if name not in index]
    _OUTPUT.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"語料 {len(names)} 部,對到 {len(mapping)} 部 -> {_OUTPUT}")
    if missing:
        # 查不到的法規不會有連結(law_urls 回 None),不是靜默略過
        print(f"查無 PCode({len(missing)} 部,這些法規不會有原文連結):{'、'.join(missing)}")


if __name__ == "__main__":
    main()
