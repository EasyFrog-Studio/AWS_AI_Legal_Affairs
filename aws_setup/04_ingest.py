"""讀三個 chunk JSONL,ingest 進 Bedrock Knowledge Base(law+interp → KB-LAW,case → KB-CASE)。
批次先試 25 筆/次,遇 ValidationException(批次過大)自動降為 10;ThrottlingException 指數退避重試 5 次。
"""
import json
import sys
import time

import boto3
from botocore.exceptions import ClientError, ParamValidationError

from config import (
    CASE_CHUNKS_PATH,
    INGEST_FAILURES_PATH,
    INTERP_CHUNKS_PATH,
    LAW_CHUNKS_PATH,
    REGION,
    load_resources,
)

# 留出法測試集年度:這些年度的決定書 chunk 不得進檢索庫,與 preprocessing/parse_decisions.py 形成兩道防線
HOLDOUT_YEARS = frozenset({"114"})

INITIAL_BATCH_SIZE = 25
FALLBACK_BATCH_SIZE = 10
MAX_THROTTLE_RETRIES = 5


def load_jsonl(path) -> list:
    if not path.exists():
        print(f"[SKIP] 找不到 {path}")
        return []
    rows = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def drop_holdout_years(rows: list) -> list:
    """濾掉 metadata.year 落在留出年度的 chunk,回傳可入庫清單。"""
    kept = [r for r in rows if str(r.get("metadata", {}).get("year", "")) not in HOLDOUT_YEARS]
    dropped = len(rows) - len(kept)
    if dropped:
        print(f"[HOLDOUT] 排除 {dropped} 筆留出年度({'/'.join(sorted(HOLDOUT_YEARS))})chunk,不入庫")
    return kept


def build_document(row: dict) -> dict:
    meta = row.get("metadata", {})
    return {
        "content": {
            "dataSourceType": "CUSTOM",
            "custom": {
                "customDocumentIdentifier": {"id": row["id"]},
                "sourceType": "IN_LINE",
                "inlineContent": {
                    "type": "TEXT",
                    "textContent": {"data": row.get("text", "")},
                },
            },
        },
        "metadata": {
            "type": "IN_LINE_ATTRIBUTE",
            "inlineAttributes": [
                # API 要求 stringValue 長度 >= 1,空值屬性直接省略該 key
                {"key": k, "value": {"type": "STRING", "stringValue": str(v)}}
                for k, v in meta.items()
                if str(v) != ""
            ],
        },
    }


def call_ingest(bedrock_agent, kb_id: str, ds_id: str, batch_docs: list):
    for attempt in range(MAX_THROTTLE_RETRIES):
        try:
            return bedrock_agent.ingest_knowledge_base_documents(
                knowledgeBaseId=kb_id,
                dataSourceId=ds_id,
                documents=batch_docs,
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "ThrottlingException" and attempt < MAX_THROTTLE_RETRIES - 1:
                wait = 2 ** attempt
                print(f"    [THROTTLE] 等待 {wait}s 後重試(第 {attempt + 1} 次)")
                time.sleep(wait)
                continue
            raise


def ingest_all(bedrock_agent, kb_id: str, ds_id: str, documents: list, kb_label: str, results: dict):
    batch_size = INITIAL_BATCH_SIZE
    i = 0
    n = len(documents)
    batch_num = 0
    while i < n:
        batch = documents[i : i + batch_size]
        try:
            call_ingest(bedrock_agent, kb_id, ds_id, batch)
            results[kb_label]["success"] += len(batch)
            i += batch_size
        except (ClientError, ParamValidationError) as e:
            code = e.response.get("Error", {}).get("Code", "") if isinstance(e, ClientError) else ""
            if code == "ValidationException" and batch_size > FALLBACK_BATCH_SIZE:
                print(f"    [VALIDATION] 批次過大,降為 {FALLBACK_BATCH_SIZE} 筆重試")
                batch_size = FALLBACK_BATCH_SIZE
                continue
            for doc in batch:
                results[kb_label]["failures"].append(
                    {
                        "kb": kb_label,
                        "id": doc["content"]["custom"]["customDocumentIdentifier"]["id"],
                        "error": str(e),
                    }
                )
            results[kb_label]["fail"] += len(batch)
            i += batch_size

        batch_num += 1
        if batch_num % 10 == 0:
            print(f"  [PROGRESS] {kb_label}: {min(i, n)}/{n}")


def main():
    resources = load_resources()
    kb_law_id = resources.get("kb_law_id")
    kb_case_id = resources.get("kb_case_id")
    ds_law_id = resources.get("data_source_law_id")
    ds_case_id = resources.get("data_source_case_id")

    if not all([kb_law_id, kb_case_id, ds_law_id, ds_case_id]):
        print(
            "[ERROR] resources.json 缺少 kb_law_id/kb_case_id/data_source_law_id/data_source_case_id,"
            "請先執行 03_vectors_kb.py",
            file=sys.stderr,
        )
        sys.exit(1)

    law_rows = load_jsonl(LAW_CHUNKS_PATH)
    interp_rows = load_jsonl(INTERP_CHUNKS_PATH)
    case_rows = drop_holdout_years(load_jsonl(CASE_CHUNKS_PATH))

    law_docs = [build_document(r) for r in law_rows + interp_rows]
    case_docs = [build_document(r) for r in case_rows]

    bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)

    results = {
        "KB-LAW": {"success": 0, "fail": 0, "failures": []},
        "KB-CASE": {"success": 0, "fail": 0, "failures": []},
    }

    print(f"開始匯入 KB-LAW(law+interp): {len(law_docs)} 筆")
    ingest_all(bedrock_agent, kb_law_id, ds_law_id, law_docs, "KB-LAW", results)

    print(f"開始匯入 KB-CASE(case): {len(case_docs)} 筆")
    ingest_all(bedrock_agent, kb_case_id, ds_case_id, case_docs, "KB-CASE", results)

    all_failures = results["KB-LAW"]["failures"] + results["KB-CASE"]["failures"]
    if all_failures:
        with INGEST_FAILURES_PATH.open("w", encoding="utf-8") as f:
            json.dump(all_failures, f, ensure_ascii=False, indent=2)

    print(
        "[DONE] "
        f"KB-LAW 成功={results['KB-LAW']['success']} 失敗={results['KB-LAW']['fail']} | "
        f"KB-CASE 成功={results['KB-CASE']['success']} 失敗={results['KB-CASE']['fail']} | "
        f"失敗清單={INGEST_FAILURES_PATH if all_failures else '無'}"
    )


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
