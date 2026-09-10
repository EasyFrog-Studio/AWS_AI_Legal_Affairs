"""解析 data/爬蟲集 的參考資料 PDF(司法院釋字 / 行政函釋 / 行政法院裁判)
-> data/爬蟲集/chunk資料/參考資料/*.jsonl,chunk 切法沿用 parse_interpretations.chunk_text。
標記 _官方_ 的檔與 data/資料集 重複,由 parse_interpretations.py 負責,此處一律跳過。
"""
import re
from pathlib import Path

from common import extract_text, write_jsonl
from parse_interpretations import chunk_text

_YIZI_RE = re.compile(r"^釋字第(\d+)號_(\d+)-(\d+)-(\d+)_(.+)$")
_HANSHI_RE = re.compile(r"^民國(\d+)年(\d+)月(\d+)日_(\S+) (.+?)_行政函釋$")
_CAIPAN_RE = re.compile(r"^(\d+)-(\d+)-(\d+)_([^_]+)_(.+)$")


def roc_date_label(year: str, month: str, day: str) -> str:
    """檔名的民國年月日 -> 與 law_chunks 既有 amend_date 同格式的字串。"""
    return f"民國 {int(year)} 年 {int(month):02d} 月 {int(day):02d} 日"


def is_official(filename: str) -> bool:
    """檔名帶 _官方_ 者是從主辦方資料集複製過來的同一份文件。"""
    return "_官方_" in filename


def parse_yizi(stem: str) -> dict:
    """"釋字第0001號_038-01-06_題旨" -> 釋字編號(不補零)、公布日期、爭點題旨。"""
    m = _YIZI_RE.match(stem)
    if not m:
        return {}
    no, year, month, day, topic = m.groups()
    return {
        "law_name": f"釋字第{int(no)}號",
        "amend_date": roc_date_label(year, month, day),
        "topic": topic,
    }


def parse_hanshi(stem: str) -> dict:
    """"民國100年03月30日_法務部 法律字第1000002151號_行政函釋" -> 發文機關與字號。"""
    m = _HANSHI_RE.match(stem)
    if not m:
        return {}
    year, month, day, issuer, doc_no = m.groups()
    return {
        "law_name": f"{issuer} {doc_no}",
        "issuer": issuer,
        "amend_date": roc_date_label(year, month, day),
    }


def parse_caipan(stem: str) -> dict:
    """"087-03-19_最高行政法院_87年度判字第427號" -> 法院與年度案號。"""
    m = _CAIPAN_RE.match(stem)
    if not m:
        return {}
    year, month, day, court, case_no = m.groups()
    return {
        "law_name": f"{court} {case_no}",
        "issuer": court,
        "amend_date": roc_date_label(year, month, day),
    }


def build_rows(doc_kind: str, stem: str, full_text: str, filename_parser) -> list[dict]:
    """一份 PDF -> law_chunks 契約的列;檔名解析不出來回空列表(由主流程計數)。"""
    parsed = filename_parser(stem)
    if not parsed:
        return []
    law_name = parsed["law_name"]
    metadata = {
        "doc_kind": doc_kind,
        "law_type": "其他",
        "law_name": law_name,
        "article_no": "",
        "amend_date": parsed["amend_date"],
        "title": stem,
        "source_file": f"{stem}.pdf",
    }
    for key in ("issuer", "topic"):
        if parsed.get(key):
            metadata[key] = parsed[key]

    pieces = chunk_text(full_text)
    rows = []
    for i, piece in enumerate(pieces, start=1):
        chunk_id = law_name if len(pieces) == 1 else f"{law_name}#p{i}"
        text = f"【{doc_kind}】{law_name}\n{piece}"
        rows.append({"id": chunk_id, "text": text, "metadata": metadata})
    return rows


REF_DIR = Path(__file__).resolve().parents[2] / "data" / "爬蟲集" / "爬蟲原資料+官方資料" / "參考資料"
CHUNK_DIR = Path(__file__).resolve().parents[2] / "data" / "爬蟲集" / "chunk資料" / "參考資料"

FOLDERS = [
    ("司法院釋字", parse_yizi),
    ("行政函釋", parse_hanshi),
    ("行政法院裁判", parse_caipan),
]


def process_folder(doc_kind: str, filename_parser) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    stats = {"檔數": 0, "官方跳過": 0, "檔名無法解析": [], "無文字層": []}
    for pdf_path in sorted((REF_DIR / doc_kind).glob("*.pdf")):
        if is_official(pdf_path.name):
            stats["官方跳過"] += 1
            continue
        stem = pdf_path.name[: -len(".pdf")]
        full_text = extract_text(pdf_path)
        if not full_text.strip():
            stats["無文字層"].append(pdf_path.name)
            continue
        built = build_rows(doc_kind, stem, full_text, filename_parser)
        if not built:
            stats["檔名無法解析"].append(pdf_path.name)
            continue
        rows.extend(built)
        stats["檔數"] += 1
    return rows, stats


def main() -> None:
    total = 0
    for doc_kind, filename_parser in FOLDERS:
        rows, stats = process_folder(doc_kind, filename_parser)
        write_jsonl(CHUNK_DIR / f"{doc_kind}.jsonl", rows)
        total += len(rows)
        print(f"[{doc_kind}] 入列檔數={stats['檔數']} chunk數={len(rows)} 官方跳過={stats['官方跳過']}")
        for label in ("檔名無法解析", "無文字層"):
            if stats[label]:
                print(f"  {label} ({len(stats[label])} 件): {stats[label][:5]}")
        ids = [r["id"] for r in rows]
        if len(ids) != len(set(ids)):
            dup = [i for i in set(ids) if ids.count(i) > 1]
            print(f"  !! 重複 id {len(dup)} 個: {dup[:5]}")
    print(f"[總計] chunk數={total},輸出於 {CHUNK_DIR}")


if __name__ == "__main__":
    main()
