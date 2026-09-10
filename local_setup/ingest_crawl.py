"""把 data/爬蟲集/chunk資料 的 JSONL 轉成本專案 schema 後灌入 Postgres。
爬蟲語料的欄位名與官方前處理產出不同(片段名/內容、clause/article/revised_date),
本檔只做映射與過濾,寫入沿用 ingest.py 的 ingest_chunks / ingest_law_articles。
"""
import re
import sys
from pathlib import Path

import httpx
import psycopg

from ingest import (
    LOCAL_LLM_BASE_URL,
    POSTGRES_URL,
    ingest_chunks,
    ingest_law_articles,
    load_jsonl,
    table_count,
)

CRAWL_DIR = Path(__file__).resolve().parents[2] / "data" / "爬蟲集" / "chunk資料"

_CLAUSE_RE = re.compile(r"^§77\((\d+)\)$")
_REVISED_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
_BUCKET_RE = re.compile(r"^決定書-(.+)\.jsonl$")

# 與 preprocessing/parse_decisions.py、ingest.py 同一份留出年度,第三道防線
HOLDOUT_YEARS = frozenset({"114"})

# 官方資料集已收且條數/修正日期驗算過的 11 部,爬蟲版同鍵會覆寫,一律不收
OFFICIAL_LAW_NAMES = frozenset({
    "民法", "行政程序法", "建築法", "訴願法", "空氣污染防制法", "廢棄物清理法",
    "行政罰法", "行政執行法", "噪音管制法", "行政院及各級行政機關訴願審議委員會審議規則",
    "洗錢防制法",
})

_PROCEDURE_LAWS = frozenset({
    "行政訴訟法", "行政程序法", "行政執行法", "行政罰法", "訴願法",
    "行政院及各級行政機關訴願審議委員會審議規則",
})
_GENERAL_LAWS = frozenset({"民法"})


def law_type_of(law_name: str) -> str:
    """F2 只排除普通法,其餘一律留下;分不出來的領域法規歸實體法而非「其他」。"""
    if law_name in _GENERAL_LAWS:
        return "普通法"
    if law_name in _PROCEDURE_LAWS:
        return "程序法"
    return "實體法"


def clause_to_appeal_article(clause: str) -> str:
    """"§77(2)" -> "77(2)";非 77 條款次(§79、複合、未判定)一律回空字串。"""
    m = _CLAUSE_RE.match(clause or "")
    return f"77({m.group(1)})" if m else ""


def roc_amend_date(revised_date: str) -> str:
    """"20251226" -> "民國 114 年 12 月 26 日";抽不出日期回「未收錄」,不猜。"""
    m = _REVISED_RE.match(revised_date or "")
    if not m:
        return "未收錄"
    year, month, day = m.groups()
    return f"民國 {int(year) - 1911} 年 {month} 月 {day} 日"


def bucket_of(filename: str) -> str:
    """"決定書-空氣污染防制法.jsonl" -> "空氣污染防制法"。"""
    m = _BUCKET_RE.match(filename)
    return m.group(1) if m else ""


def map_case_row(row: dict, bucket: str) -> dict | None:
    """爬蟲決定書 chunk -> case_chunks 契約;留出年度回 None(不入庫)。"""
    src = row.get("metadata", {})
    year = str(src.get("year", ""))
    if year in HOLDOUT_YEARS:
        return None
    section = src.get("section", "")
    clause = src.get("clause", "")
    result = src.get("result", "")
    reason = src.get("case_type", "")
    # header 沿用來源案由:它已進過 embedding,改了會讓重跑結果與既有向量不一致
    header = f"【{year}年-{reason}-{clause}-{result}】{section}欄"
    return {
        "id": row["片段名"],
        "text": f"{header}\n{row.get('內容', '')}",
        "metadata": {
            "year": year,
            # 來源的 case_type 是自由文字案由(語料 630 種),F3 完全相等過濾對它必然落空
            "case_type": bucket,
            "case_subtype": reason,
            "appeal_article": clause_to_appeal_article(clause),
            "issue": "",  # 爬蟲語料無爭點欄;F3 第二層過濾在這批資料上不生效
            "result": result,
            "section": section,
            "case_no": src.get("case_no", ""),
            "source_file": src.get("source_file", ""),
            "source_url": src.get("source_url", ""),
        },
    }


def map_law_row(row: dict) -> dict | None:
    """爬蟲法條 chunk -> law_chunks/law_articles 契約;刪除條文與官方已收法規回 None。"""
    src = row.get("metadata", {})
    law_name = src.get("law_name", "")
    if src.get("deleted") == "true" or law_name in OFFICIAL_LAW_NAMES:
        return None
    article_no = src.get("article", "")
    return {
        "id": f"{law_name}#{article_no}",
        "text": row.get("內容", ""),
        "metadata": {
            "law_name": law_name,
            "article_no": article_no,
            "amend_date": roc_amend_date(src.get("revised_date", "")),
            "law_type": law_type_of(law_name),
            "doc_kind": "法規",
            "source_file": row.get("來源檔", ""),
        },
    }


def collect_cases() -> tuple[list, int]:
    """決定書:案型分桶來自檔名,回傳 (可入庫列, 留出年度排除筆數)。"""
    rows, refused = [], 0
    for path in sorted((CRAWL_DIR / "訴願決定書").glob("*.jsonl")):
        bucket = bucket_of(path.name)
        for src in load_jsonl(path):
            mapped = map_case_row(src, bucket)
            if mapped is None:
                refused += 1
            else:
                rows.append(mapped)
    return rows, refused


def collect_laws() -> tuple[list, int]:
    """法條:回傳 (可入庫列, 刪除條文與官方已收的排除筆數)。"""
    rows, refused = [], 0
    for path in sorted((CRAWL_DIR / "法條").glob("*.jsonl")):
        for src in load_jsonl(path):
            mapped = map_law_row(src)
            if mapped is None:
                refused += 1
            else:
                rows.append(mapped)
    return rows, refused


def reference_rows() -> list[dict]:
    """參考資料 chunk 由 parse_crawl_reference.py 直接產成本專案 schema,原樣載入。"""
    rows = []
    for path in sorted((CRAWL_DIR / "參考資料").glob("*.jsonl")):
        rows.extend(load_jsonl(path))
    return rows


def main() -> None:
    case_rows, case_refused = collect_cases()
    law_rows, law_refused = collect_laws()
    ref_rows = reference_rows()
    print(f"決定書:可入庫 {len(case_rows)} 筆,留出年度排除 {case_refused} 筆")
    print(f"法條  :可入庫 {len(law_rows)} 筆,刪除條文與官方已收排除 {law_refused} 筆")
    print(f"參考資料:可入庫 {len(ref_rows)} 筆(釋字/函釋/裁判,不進 law_articles 精查表)")

    conn = psycopg.connect(POSTGRES_URL)
    try:
        with httpx.Client(base_url=LOCAL_LLM_BASE_URL, timeout=300) as http_client:
            ingest_chunks(conn, http_client, "law_chunks", law_rows + ref_rows, "law_chunks(爬蟲)")
            ingest_chunks(conn, http_client, "case_chunks", case_rows, "case_chunks(爬蟲)")
        # 參考資料無條號,鍵是「法名#條號」的精查表收不了它們
        ingest_law_articles(conn, law_rows)
        print(
            "[DONE] "
            f"law_chunks={table_count(conn, 'law_chunks')} "
            f"case_chunks={table_count(conn, 'case_chunks')} "
            f"law_articles={table_count(conn, 'law_articles')}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as e:
        print(f"[ERROR] ollama 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
    except psycopg.Error as e:
        print(f"[ERROR] Postgres 錯誤: {e}", file=sys.stderr)
        sys.exit(1)
