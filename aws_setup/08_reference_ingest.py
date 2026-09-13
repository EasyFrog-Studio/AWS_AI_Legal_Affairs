"""讀參考見解三類(司法院釋字/行政函釋/行政法院裁判)的 chunk JSONL,逐 chunk ingest 進對應 KB,
逐文件彙整寫入對應 DynamoDB 表,並上傳 DynamoDB item 實際引用到的原始 PDF 到 S3。
假設 KB/DataSource/DynamoDB 表已由骨架腳本建好,本腳本只負責灌資料,可重複執行。
"""
import argparse
import csv
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, ParamValidationError

_SCRIPT_DIR = Path(__file__).resolve().parent
_SPEC = importlib.util.spec_from_file_location(
    "law_sample_reset", _SCRIPT_DIR / "05_reset_law_sample.py"
)
base = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = base
_SPEC.loader.exec_module(base)

from config import (  # noqa: E402
    INGEST_FAILURES_PATH,
    REFERENCE_CHUNK_DIR,
    REFERENCE_KINDS,
    REFERENCE_PDF_DIR,
    REFERENCE_S3_PREFIX,
    REGION,
    S3_BUCKET,
    load_resources,
)

# doc_kind → resources.json 的 kb_{slug}_id / data_source_{slug}_id key 後綴
DOC_KIND_SLUGS = {
    "司法院釋字": "interpretation",
    "行政函釋": "ruling",
    "行政法院裁判": "judgment",
}

INITIAL_BATCH_SIZE = 10  # IngestKnowledgeBaseDocuments 的 documents 上限就是 10
FALLBACK_BATCH_SIZE = 10
MAX_THROTTLE_RETRIES = 5
# 已在跑或已完成的都不必重送;其餘狀態(含失敗)重試
_DONE_STATUSES = {"INDEXED", "IN_PROGRESS"}

# 多塊文件的 chunk id 形如 {law_name}#p{n};單塊文件 id == law_name,視為 p0
_PAGE_RE = re.compile(r"#p(\d+)$")


def load_source_urls() -> dict:
    """原文連結 CSV -> {檔名: 原文網址};檔名與 chunk metadata 的 source_file 同一個值。
    CSV 不存在時回空 dict,`source_url` 一律留空字串,不讓缺連結擋掉整批灌庫。"""
    path = base.SOURCE_LINK_CSV
    if not path.exists():
        print(f"[WARN] 找不到原文連結 CSV:{path};source_url 全部留空")
        return {}
    urls = {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("類別", "").strip() != "參考資料":
                continue
            file_name = row.get("檔名", "").strip()
            url = row.get("原文網址", "").strip()
            if file_name and url.startswith(("https://", "http://")):
                urls.setdefault(file_name, url)
    return urls


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


def _page_no(chunk_id: str) -> int:
    m = _PAGE_RE.search(chunk_id)
    return int(m.group(1)) if m else 0


def group_by_document(rows: list, url_by_file: dict) -> list:
    """依 metadata.law_name 把同一份文件的多個 chunk 併成一筆 DynamoDB item,保留首次出現順序。"""
    groups: dict = {}
    order = []
    for row in rows:
        law_name = row.get("metadata", {}).get("law_name", "")
        if law_name not in groups:
            groups[law_name] = []
            order.append(law_name)
        groups[law_name].append(row)

    docs = []
    for law_name in order:
        chunks = sorted(groups[law_name], key=lambda r: _page_no(r["id"]))
        meta = chunks[0].get("metadata", {})
        doc_kind = meta.get("doc_kind", "")
        source_file = meta.get("source_file", "")
        docs.append(
            {
                "ref_id": law_name,
                "doc_kind": doc_kind,
                "name": law_name,
                "issuer": meta.get("issuer", ""),
                "issued_date": meta.get("amend_date", ""),
                "topic": meta.get("topic", ""),
                "title": meta.get("title", ""),
                "source_file": source_file,
                "s3_key": f"{REFERENCE_S3_PREFIX}/{doc_kind}/{source_file}",
                "source_url": url_by_file.get(source_file, ""),
                "full_text": "\n".join(r.get("text", "") for r in chunks),
                "chunk_count": len(chunks),
                "chunk_ids": [r["id"] for r in chunks],
            }
        )
    return docs


def pending_rows(rows: list, status_by_id: dict) -> list:
    """只留下還沒 INDEXED / IN_PROGRESS 的 chunk,讓中斷後重跑不必整類重送。"""
    return [r for r in rows if status_by_id.get(r["id"]) not in _DONE_STATUSES]


def kb_status_by_id(bedrock_agent, limiter, kb_id: str, ds_id: str) -> dict:
    details = base.list_kb_documents(bedrock_agent, limiter, kb_id, ds_id)
    return {
        d["identifier"].get("custom", {}).get("id"): d.get("status", "")
        for d in details
    }


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
                    "textContent": {"data": row["text"]},
                },
            },
        },
        "metadata": {
            "type": "IN_LINE_ATTRIBUTE",
            "inlineAttributes": [
                {"key": "ref_id", "value": {"type": "STRING", "stringValue": meta["law_name"]}},
                {"key": "doc_kind", "value": {"type": "STRING", "stringValue": meta["doc_kind"]}},
            ],
        },
    }


def call_ingest(bedrock_agent, kb_id: str, ds_id: str, batch_docs: list, limiter):
    for attempt in range(MAX_THROTTLE_RETRIES):
        try:
            return limiter.call(
                bedrock_agent.ingest_knowledge_base_documents,
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


def ingest_all(bedrock_agent, kb_id: str, ds_id: str, documents: list, kb_label: str, results: dict, limiter):
    batch_size = INITIAL_BATCH_SIZE
    i = 0
    n = len(documents)
    batch_num = 0
    while i < n:
        batch = documents[i : i + batch_size]
        try:
            call_ingest(bedrock_agent, kb_id, ds_id, batch, limiter)
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


def write_dynamodb(resource, table_name: str, docs: list) -> int:
    table = resource.Table(table_name)
    count = 0
    with table.batch_writer() as batch:
        for doc in docs:
            batch.put_item(Item=doc)
            count += 1
    return count


def object_exists(s3, key: str) -> bool:
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=key)
        return True
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def upload_pdfs(s3, doc_kind: str, docs: list, counters: dict, failures: list):
    for doc in docs:
        local_path = REFERENCE_PDF_DIR / doc_kind / doc["source_file"]
        key = doc["s3_key"]
        if not local_path.exists():
            counters["fail"] += 1
            failures.append(
                {"doc_kind": doc_kind, "ref_id": doc["ref_id"], "source_file": doc["source_file"], "error": "來源 PDF 不存在"}
            )
            print(f"    [ERROR] 找不到來源 PDF: {local_path}")
            continue
        if object_exists(s3, key):
            counters["skip"] += 1
            continue
        s3.upload_file(str(local_path), S3_BUCKET, key)
        counters["upload"] += 1


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=[k[0] for k in REFERENCE_KINDS], default=None)
    parser.add_argument("--skip-s3", action="store_true")
    return parser.parse_args()


def main():
    args = parse_args()
    resources = load_resources()
    kinds = tuple(k for k in REFERENCE_KINDS if k[0] == args.only) if args.only else REFERENCE_KINDS

    bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)
    ddb_resource = boto3.resource("dynamodb", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)
    limiter = base.RateLimiter(base.BEDROCK_MIN_INTERVAL_SECONDS)
    url_by_file = load_source_urls()
    print(f"原文連結對照:{len(url_by_file)} 筆")

    all_failures = []
    totals = {"kb_success": 0, "kb_fail": 0, "ddb_count": 0, "s3_upload": 0, "s3_skip": 0, "s3_fail": 0}

    for doc_kind, kb_name, index_name, ddb_table in kinds:
        slug = DOC_KIND_SLUGS[doc_kind]
        kb_id = resources.get(f"kb_{slug}_id")
        ds_id = resources.get(f"data_source_{slug}_id")
        if not kb_id or not ds_id:
            print(
                f"[ERROR] resources.json 缺少 kb_{slug}_id/data_source_{slug}_id,請先執行骨架腳本建好 {kb_name}",
                file=sys.stderr,
            )
            sys.exit(1)

        rows = load_jsonl(REFERENCE_CHUNK_DIR / f"{doc_kind}.jsonl")
        status_by_id = kb_status_by_id(bedrock_agent, limiter, kb_id, ds_id)
        todo = pending_rows(rows, status_by_id)
        print(
            f"開始處理 {doc_kind}: 語料 {len(rows)} chunk;"
            f"KB 現有 {len(status_by_id)};需提交 {len(todo)}"
        )

        results = {doc_kind: {"success": 0, "fail": 0, "failures": []}}
        ingest_all(
            bedrock_agent, kb_id, ds_id, [build_document(r) for r in todo], doc_kind, results, limiter
        )

        docs = group_by_document(rows, url_by_file)
        ddb_count = write_dynamodb(ddb_resource, ddb_table, docs)

        s3_counters = {"upload": 0, "skip": 0, "fail": 0}
        if args.skip_s3:
            print("  [SKIP] --skip-s3,略過 PDF 上傳")
        else:
            upload_pdfs(s3, doc_kind, docs, s3_counters, all_failures)

        all_failures.extend(results[doc_kind]["failures"])

        totals["kb_success"] += results[doc_kind]["success"]
        totals["kb_fail"] += results[doc_kind]["fail"]
        totals["ddb_count"] += ddb_count
        totals["s3_upload"] += s3_counters["upload"]
        totals["s3_skip"] += s3_counters["skip"]
        totals["s3_fail"] += s3_counters["fail"]

        no_url = sum(1 for d in docs if not d["source_url"])
        print(
            f"[DONE] {doc_kind}: KB 成功={results[doc_kind]['success']} 失敗={results[doc_kind]['fail']} | "
            f"DDB 筆數={ddb_count}(無原文連結 {no_url}) | "
            f"S3 上傳={s3_counters['upload']} 跳過={s3_counters['skip']} 失敗={s3_counters['fail']}"
        )

    if all_failures:
        with INGEST_FAILURES_PATH.open("w", encoding="utf-8") as f:
            json.dump(all_failures, f, ensure_ascii=False, indent=2)

    print(
        "[總計] "
        f"KB 成功={totals['kb_success']} 失敗={totals['kb_fail']} | "
        f"DDB 筆數={totals['ddb_count']} | "
        f"S3 上傳={totals['s3_upload']} 跳過={totals['s3_skip']} 失敗={totals['s3_fail']} | "
        f"失敗清單={INGEST_FAILURES_PATH if all_failures else '無'}"
    )


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
