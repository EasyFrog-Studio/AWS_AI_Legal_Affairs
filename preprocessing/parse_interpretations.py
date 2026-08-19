"""解析 行政函釋(10 檔) 與 司法院釋字及行政判解(19 檔) PDF,
輸出 data/output/interp_chunks.jsonl + data/output/markdown/{行政函釋,判解}/*.md。
"""
import re

from common import DATASET_DIR, OUTPUT_DIR, clean_filename, extract_text, write_jsonl, write_markdown

CHUNK_LIMIT = 1500
FULL_TEXT_THRESHOLD = 2000
ID_MAX_LEN = 60

# ---- 檔名解析 ----

# 行政函釋: {issuer}{date}{doc_no}函[釋]-{topic}
#   例: 內政部100年12月9日內授營建管字第1000810874號函釋-場所區隔方式
#   例: 法務部93年4月13日法律字0930014628號函-寄存送達 (無「第」、無「釋」)
INTERP_RE = re.compile(
    r"^(?P<issuer>[^\d]+?)(?P<date>\d+年\d+月\d+日)(?P<doc_no>.+?號)函釋?-(?P<topic>.+)$"
)

# 判解/判決: {court}{year}[年]度{case_type}第{no}號{doc_type}-{topic}
#   例: 最高行政法院102年度判字第147號行政判決-政府資訊公開法精神
#   例: 最高行政法院109度上字第817號判決-政府資訊公開法18條2項 (無「年」)
JUDGMENT_RE = re.compile(
    r"^(?P<issuer>[^\d]+?)(?P<year>\d+)年?度(?P<case_no>[^\d]+\d+號)(?P<doc_type>[^-]+)-(?P<topic>.+)$"
)

# 釋字: 釋字第{no}號解釋-{topic}
YIZI_RE = re.compile(r"^釋字第(?P<no>\d+)號(?P<doc_type>解釋)-(?P<topic>.+)$")


def parse_interp_filename(stem: str) -> dict:
    m = INTERP_RE.match(stem)
    if not m:
        return {}
    d = m.groupdict()
    return {
        "issuer": d["issuer"],
        "date": d["date"],
        "doc_no": d["doc_no"],
        "topic": d["topic"],
    }


def parse_panjie_filename(stem: str) -> dict:
    m = JUDGMENT_RE.match(stem)
    if m:
        d = m.groupdict()
        return {
            "issuer": d["issuer"],
            "doc_no": f"{d['year']}年度{d['case_no']}",
            "topic": d["topic"],
        }
    m = YIZI_RE.match(stem)
    if m:
        d = m.groupdict()
        return {
            "doc_no": f"釋字第{d['no']}號",
            "topic": d["topic"],
        }
    return {}


# ---- Chunking ----

def _split_units(text: str) -> list[str]:
    """優先以空行切段落;若全文無空行,退而以單行為切分單位。"""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    if len(paragraphs) > 1:
        return paragraphs
    lines = [l.strip() for l in text.strip().splitlines() if l.strip()]
    return lines if lines else [text.strip()]


def chunk_text(text: str) -> list[str]:
    """全文 <2000 字 → 整篇一筆;否則依段落切約 1500 字/筆。"""
    text = text.strip()
    if len(text) < FULL_TEXT_THRESHOLD:
        return [text]

    units = _split_units(text)
    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + 1 + len(unit) > CHUNK_LIMIT:
            chunks.append(current)
            current = unit
        else:
            current = f"{current}\n{unit}" if current else unit
    if current:
        chunks.append(current)
    return chunks


# ---- 主流程 ----

def process_folder(folder_name: str, doc_kind: str, markdown_category: str, filename_parser):
    folder = DATASET_DIR / folder_name
    pdf_files = sorted(folder.glob("*.pdf"))

    rows = []
    stats = {
        "file_count": 0,
        "chunk_count": 0,
        "field_hits": {"issuer": 0, "date": 0, "doc_no": 0, "topic": 0},
        "unparsed_files": [],
    }

    for pdf_path in pdf_files:
        cleaned = clean_filename(pdf_path.name)
        title = cleaned[:-4] if cleaned.lower().endswith(".pdf") else cleaned
        source_file = pdf_path.name

        extra = filename_parser(title)
        if not extra:
            stats["unparsed_files"].append(source_file)
        for k in stats["field_hits"]:
            if extra.get(k):
                stats["field_hits"][k] += 1

        full_text = extract_text(pdf_path)

        metadata = {
            "doc_kind": doc_kind,
            "law_type": "其他",
            "title": title,
            "source_file": source_file,
        }
        metadata.update(extra)

        pieces = chunk_text(full_text)
        base_id = title[:ID_MAX_LEN]
        for i, piece in enumerate(pieces, start=1):
            chunk_id = base_id if len(pieces) == 1 else f"{base_id}#p{i}"
            text = f"【{doc_kind}】{title}\n{piece}"
            rows.append({"id": chunk_id, "text": text, "metadata": metadata})

        stats["file_count"] += 1
        stats["chunk_count"] += len(pieces)

        md_content = f"# {title}\n\n" + "\n".join(f"- {k}: {v}" for k, v in metadata.items()) + f"\n\n---\n\n{full_text}"
        write_markdown(markdown_category, title, md_content)

    return rows, stats


def main():
    interp_rows, interp_stats = process_folder(
        "行政函釋", "行政函釋", "行政函釋", parse_interp_filename
    )
    panjie_rows, panjie_stats = process_folder(
        "司法院釋字及行政判解", "判解", "判解", parse_panjie_filename
    )

    all_rows = interp_rows + panjie_rows
    write_jsonl(OUTPUT_DIR / "interp_chunks.jsonl", all_rows)

    def print_stats(name, stats):
        n = stats["file_count"]
        print(f"[{name}] 檔數={n} chunk數={stats['chunk_count']}")
        for field, hits in stats["field_hits"].items():
            pct = (hits / n * 100) if n else 0
            print(f"  {field} 覆蓋率: {hits}/{n} ({pct:.0f}%)")
        if stats["unparsed_files"]:
            print(f"  無法解析檔名的檔案: {stats['unparsed_files']}")

    print_stats("行政函釋", interp_stats)
    print_stats("判解", panjie_stats)
    print(f"[總計] 檔數={interp_stats['file_count'] + panjie_stats['file_count']} "
          f"chunk數={interp_stats['chunk_count'] + panjie_stats['chunk_count']}")


if __name__ == "__main__":
    main()
