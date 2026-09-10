"""解析 行政函釋(10 檔) 與 司法院釋字及行政判解(19 檔) PDF,
輸出 data/output/interp_chunks.jsonl + data/output/markdown/{行政函釋,司法院釋字,行政法院裁判}/*.md。
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


# 民國年月日 -> 與爬蟲語料 amend_date 同格式
_ROC_DATE_RE = re.compile(r"^(\d+)年(\d+)月(\d+)日$")


def roc_date_label(date_str: str) -> str:
    m = _ROC_DATE_RE.match(date_str)
    if not m:
        return ""
    year, month, day = m.groups()
    return f"民國 {int(year)} 年 {int(month):02d} 月 {int(day):02d} 日"


def parse_filename(stem: str) -> dict:
    """檔名 -> metadata 附加欄位(含 doc_kind);解析不出來回空 dict。
    欄位名與 parse_crawl_reference.build_rows 一致,兩批語料才是同一份契約。"""
    m = INTERP_RE.match(stem)
    if m:
        d = m.groupdict()
        parsed = {
            "doc_kind": "行政函釋",
            "law_name": f"{d['issuer']} {d['doc_no']}",
            "issuer": d["issuer"],
            "topic": d["topic"],
        }
        # 官方釋字與裁判的檔名不帶日期,缺就是缺,由檢索端填「未收錄」
        amend_date = roc_date_label(d["date"])
        if amend_date:
            parsed["amend_date"] = amend_date
        return parsed

    m = YIZI_RE.match(stem)
    if m:
        d = m.groupdict()
        return {
            "doc_kind": "司法院釋字",
            "law_name": f"釋字第{int(d['no'])}號",
            "topic": d["topic"],
        }

    m = JUDGMENT_RE.match(stem)
    if m:
        d = m.groupdict()
        return {
            "doc_kind": "行政法院裁判",
            "law_name": f"{d['issuer']} {d['year']}年度{d['case_no']}",
            "issuer": d["issuer"],
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


# ---- 一份文件 -> chunk 列 ----

def build_rows(stem: str, full_text: str) -> list[dict]:
    """一份文件的檔名與全文 -> chunk 列;檔名解析不出來回空列表(由主流程計數)。"""
    parsed = parse_filename(stem)
    if not parsed:
        return []
    doc_kind = parsed["doc_kind"]

    metadata = {
        "law_type": "其他",
        "article_no": "",  # 參考見解沒有條號,但欄位要在,契約才與法規 chunk 對得起來
        "title": stem,
        # 指向前處理產出的 markdown:取原文的端點只在 data/output/ 下找,填 PDF 檔名一律查無
        "source_file": f"markdown/{doc_kind}/{stem}.md",
    }
    metadata.update(parsed)

    pieces = chunk_text(full_text)
    base_id = stem[:ID_MAX_LEN]
    rows = []
    for i, piece in enumerate(pieces, start=1):
        chunk_id = base_id if len(pieces) == 1 else f"{base_id}#p{i}"
        rows.append(
            {"id": chunk_id, "text": f"【{doc_kind}】{stem}\n{piece}", "metadata": metadata}
        )
    return rows


# ---- 主流程 ----

def process_folder(folder_name: str):
    """一個來源資料夾 -> (chunk 列, 統計)。doc_kind 由各檔檔名決定,不再是資料夾層級的固定值。"""
    rows = []
    stats = {"file_count": 0, "chunk_count": 0, "by_doc_kind": {}, "unparsed_files": []}

    for pdf_path in sorted((DATASET_DIR / folder_name).glob("*.pdf")):
        cleaned = clean_filename(pdf_path.name)
        stem = cleaned[:-4] if cleaned.lower().endswith(".pdf") else cleaned

        full_text = extract_text(pdf_path)
        built = build_rows(stem, full_text)
        if not built:
            stats["unparsed_files"].append(pdf_path.name)
            continue

        rows.extend(built)
        metadata = built[0]["metadata"]
        doc_kind = metadata["doc_kind"]
        stats["file_count"] += 1
        stats["chunk_count"] += len(built)
        stats["by_doc_kind"][doc_kind] = stats["by_doc_kind"].get(doc_kind, 0) + len(built)

        md_content = (
            f"# {stem}\n\n"
            + "\n".join(f"- {k}: {v}" for k, v in metadata.items())
            + f"\n\n---\n\n{full_text}"
        )
        # 目錄名必須與 metadata.source_file 的第二段一致,取原文的端點才找得到
        write_markdown(doc_kind, stem, md_content)

    return rows, stats


def main():
    all_rows, all_stats = [], []
    for folder_name in ("行政函釋", "司法院釋字及行政判解"):
        rows, stats = process_folder(folder_name)
        all_rows.extend(rows)
        all_stats.append((folder_name, stats))

    write_jsonl(OUTPUT_DIR / "interp_chunks.jsonl", all_rows)

    for folder_name, stats in all_stats:
        print(f"[{folder_name}] 檔數={stats['file_count']} chunk數={stats['chunk_count']}")
        for doc_kind, count in sorted(stats["by_doc_kind"].items()):
            print(f"  {doc_kind}: {count} chunk")
        if stats["unparsed_files"]:
            # 解析不出檔名的檔案整份不入列,不能只是印個覆蓋率了事
            print(f"  [警告] 檔名無法解析、未入列: {stats['unparsed_files']}")

    missing = sum(len(s["unparsed_files"]) for _, s in all_stats)
    print(f"[總計] 檔數={sum(s['file_count'] for _, s in all_stats)} "
          f"chunk數={len(all_rows)} 未入列={missing}")


if __name__ == "__main__":
    main()
