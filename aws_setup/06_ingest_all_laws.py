"""將全部有效現行法條追加到已確認格式的 DynamoDB 與 Bedrock KB。

特性：
- 保留 05_reset_law_sample.py 固定種子選出的 law_id 1..10。
- 其餘法條依法規名稱、數字條號、分類固定排序，從 law_id 11 起編號。
- DynamoDB 全量冪等覆寫；KB 只提交不存在或失敗的 document ID，可中斷續跑。
- 所有 Bedrock 請求共用 1.1 秒節流器，嚴格低於 1 RPS。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

_SCRIPT_DIR = Path(__file__).resolve().parent
_SAMPLE_SCRIPT = _SCRIPT_DIR / "05_reset_law_sample.py"
_spec = importlib.util.spec_from_file_location("law_sample_reset", _SAMPLE_SCRIPT)
if _spec is None or _spec.loader is None:
    raise RuntimeError(f"無法載入 {_SAMPLE_SCRIPT}")
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)

from config import ACCOUNT_ID, DDB_LAW_TABLE, KB_LAW_NAME, REGION, load_resources  # noqa: E402

EXECUTE_CONFIRMATION = "INGEST-ALL-LAWS"
INGEST_BATCH_SIZE = 10
PROGRESS_EVERY = 100
WAIT_TIMEOUT_SECONDS = 1800


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        metavar="CONFIRMATION",
        help=f"真正寫入 AWS 時必須給定精確字串 {EXECUTE_CONFIRMATION}",
    )
    return parser.parse_args()


def article_sort_key(article_no: str) -> tuple[int, ...]:
    return tuple(int(part) for part in article_no.split("-"))


def assign_all_ids(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    approved_sample = base.choose_sample(candidates)
    approved_keys = {item["law_article"] for item in approved_sample}
    remaining = sorted(
        (item for item in candidates if item["law_article"] not in approved_keys),
        key=lambda item: (
            item["law_name"],
            article_sort_key(item["article_no"]),
            item["category_a"],
        ),
    )
    all_items = list(approved_sample)
    for law_id, item in enumerate(remaining, base.SAMPLE_SIZE + 1):
        item["law_id"] = law_id
        item["document_id"] = f"LAW#{law_id:08d}"
        all_items.append(item)
    if len({item["law_id"] for item in all_items}) != len(all_items):
        raise RuntimeError("law_id 不唯一")
    if len({item["law_article"] for item in all_items}) != len(all_items):
        raise RuntimeError("law_article 不唯一")
    return all_items


def print_plan(items: list[dict[str, Any]], missing_link_laws: list[str]) -> None:
    counts = Counter(item["category_a"] for item in items)
    no_link_items = [item for item in items if not item["source_url"]]
    print(
        json.dumps(
            {
                "total_items": len(items),
                "law_id_range": [items[0]["law_id"], items[-1]["law_id"]],
                "category_counts": dict(sorted(counts.items())),
                "items_without_direct_url": len(no_link_items),
                "laws_without_direct_url": missing_link_laws,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def assert_live_scope(session: boto3.Session, resources: dict[str, Any]) -> tuple[str, str]:
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != ACCOUNT_ID:
        raise RuntimeError(
            f"AWS 帳號不符：預期 {ACCOUNT_ID}，實際 {identity['Account']}"
        )
    kb_id = resources.get("kb_law_id")
    data_source_id = resources.get("data_source_law_id")
    if not kb_id or not data_source_id:
        raise RuntimeError("resources.json 缺少法規 KB/data source ID")

    table = session.client("dynamodb").describe_table(TableName=DDB_LAW_TABLE)["Table"]
    if table["TableStatus"] != "ACTIVE":
        raise RuntimeError(f"{DDB_LAW_TABLE} 狀態不是 ACTIVE")
    if table["KeySchema"] != [{"AttributeName": "law_id", "KeyType": "HASH"}]:
        raise RuntimeError(f"DynamoDB 主鍵不是 law_id：{table['KeySchema']}")

    limiter = base.RateLimiter(base.BEDROCK_MIN_INTERVAL_SECONDS)
    bedrock = session.client("bedrock-agent")
    kb = limiter.call(bedrock.get_knowledge_base, knowledgeBaseId=kb_id)["knowledgeBase"]
    if kb["name"] != KB_LAW_NAME or kb["status"] != "ACTIVE":
        raise RuntimeError(f"法規 KB 不正確：{kb['name']} status={kb['status']}")
    data_source = limiter.call(
        bedrock.get_data_source,
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
    )["dataSource"]
    if data_source["status"] != "AVAILABLE":
        raise RuntimeError(f"法規 data source 狀態不是 AVAILABLE：{data_source['status']}")
    print(
        f"[VERIFY] account={identity['Account']} region={REGION} "
        f"table={DDB_LAW_TABLE} KB={kb_id} dataSource={data_source_id}"
    )
    return kb_id, data_source_id


def write_all_dynamodb(session: boto3.Session, items: list[dict[str, Any]]) -> None:
    table = session.resource("dynamodb").Table(DDB_LAW_TABLE)
    with table.batch_writer(overwrite_by_pkeys=["law_id"]) as batch:
        for index, source in enumerate(items, 1):
            item = {key: source[key] for key in base.DYNAMODB_FIELDS}
            item["law_id"] = Decimal(source["law_id"])
            batch.put_item(Item=item)
            if index % 1000 == 0:
                print(f"  [DDB] 已寫入 {index}/{len(items)}")
    print(f"[DDB] 全量寫入完成：{len(items)}")


def scan_all_dynamodb(session: boto3.Session) -> list[dict[str, Any]]:
    table = session.resource("dynamodb").Table(DDB_LAW_TABLE)
    response = table.scan(ConsistentRead=True)
    items = response.get("Items", [])
    while response.get("LastEvaluatedKey"):
        response = table.scan(
            ConsistentRead=True,
            ExclusiveStartKey=response["LastEvaluatedKey"],
        )
        items.extend(response.get("Items", []))
    return items


def verify_all_dynamodb(
    session: boto3.Session,
    expected: list[dict[str, Any]],
) -> None:
    actual = scan_all_dynamodb(session)
    expected_by_id = {Decimal(item["law_id"]): item for item in expected}
    actual_by_id = {item["law_id"]: item for item in actual}
    if len(actual) != len(expected) or set(actual_by_id) != set(expected_by_id):
        raise RuntimeError(
            f"DynamoDB 數量/ID 不符：expected={len(expected)} actual={len(actual)}"
        )
    expected_fields = set(base.DYNAMODB_FIELDS)
    for law_id, source in expected_by_id.items():
        item = actual_by_id[law_id]
        if set(item) != expected_fields:
            raise RuntimeError(
                f"DynamoDB law_id={law_id} 欄位不符：{sorted(item)}"
            )
        for key in expected_fields - {"law_id"}:
            if item[key] != source[key]:
                raise RuntimeError(f"DynamoDB law_id={law_id} 欄位 {key} 不一致")
    print(f"[VERIFY] DynamoDB 恰有 {len(actual)} 筆，ID 與 {len(expected_fields)} 欄資料全部一致")


def list_kb_documents(
    bedrock: Any,
    limiter: Any,
    kb_id: str,
    data_source_id: str,
) -> list[dict[str, Any]]:
    return base.list_kb_documents(bedrock, limiter, kb_id, data_source_id)


def ingest_missing_kb(
    bedrock: Any,
    limiter: Any,
    kb_id: str,
    data_source_id: str,
    items: list[dict[str, Any]],
) -> None:
    existing_details = list_kb_documents(bedrock, limiter, kb_id, data_source_id)
    status_by_id = {
        detail["identifier"].get("custom", {}).get("id"): detail.get("status", "")
        for detail in existing_details
    }
    pending_items = [
        item
        for item in items
        if status_by_id.get(item["document_id"]) not in {"INDEXED", "IN_PROGRESS"}
    ]
    print(
        f"[KB] 現有={len(existing_details)}；需提交/重試={len(pending_items)}；"
        f"已 INDEXED={sum(s == 'INDEXED' for s in status_by_id.values())}"
    )

    submitted = 0
    for start in range(0, len(pending_items), INGEST_BATCH_SIZE):
        batch_items = pending_items[start : start + INGEST_BATCH_SIZE]
        documents = [base.kb_document(item) for item in batch_items]
        for attempt in range(8):
            try:
                limiter.call(
                    bedrock.ingest_knowledge_base_documents,
                    knowledgeBaseId=kb_id,
                    dataSourceId=data_source_id,
                    documents=documents,
                )
                break
            except ClientError as error:
                code = error.response.get("Error", {}).get("Code", "")
                if code not in {"ThrottlingException", "ConflictException", "ValidationException"}:
                    raise
                if attempt == 7:
                    raise
                wait = min(30, 2 ** attempt)
                print(f"  [KB RETRY] {code}，等待 {wait}s")
                time.sleep(wait)
        submitted += len(batch_items)
        if submitted % PROGRESS_EVERY == 0 or submitted == len(pending_items):
            print(f"  [KB] 已提交 {submitted}/{len(pending_items)}")


def wait_and_verify_kb(
    bedrock: Any,
    limiter: Any,
    kb_id: str,
    data_source_id: str,
    expected: list[dict[str, Any]],
) -> None:
    expected_ids = {item["document_id"] for item in expected}
    deadline = time.monotonic() + WAIT_TIMEOUT_SECONDS
    while True:
        details = list_kb_documents(bedrock, limiter, kb_id, data_source_id)
        actual_ids = {
            detail["identifier"].get("custom", {}).get("id") for detail in details
        }
        statuses = Counter(detail.get("status", "UNKNOWN") for detail in details)
        failed = [
            detail
            for detail in details
            if "FAILED" in detail.get("status", "")
        ]
        if failed:
            raise RuntimeError(f"KB 有 {len(failed)} 筆失敗：{failed[:3]}")
        if actual_ids == expected_ids and statuses.get("INDEXED", 0) == len(expected):
            print(f"[VERIFY] KB 恰有 {len(details)} 筆且全部 INDEXED")
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"等待 KB 全量 INDEXED 逾時：count={len(details)} statuses={dict(statuses)}"
            )
        print(f"  [KB WAIT] count={len(details)} statuses={dict(statuses)}")
        time.sleep(15)


def main() -> None:
    args = parse_args()
    candidates, missing_link_laws = base.load_candidates()
    items = assign_all_ids(candidates)
    print_plan(items, missing_link_laws)
    if len(items) != 13010:
        raise RuntimeError(f"全量筆數不是預期 13010：{len(items)}")

    if args.execute is None:
        print(f"[DRY-RUN] 未呼叫 AWS；要執行須傳 --execute {EXECUTE_CONFIRMATION}")
        return
    if args.execute != EXECUTE_CONFIRMATION:
        raise SystemExit(f"確認字串錯誤；必須是 {EXECUTE_CONFIRMATION}")

    session = boto3.Session(region_name=REGION)
    resources = load_resources()
    kb_id, data_source_id = assert_live_scope(session, resources)
    write_all_dynamodb(session, items)
    verify_all_dynamodb(session, items)

    bedrock = session.client("bedrock-agent")
    limiter = base.RateLimiter(base.BEDROCK_MIN_INTERVAL_SECONDS)
    ingest_missing_kb(bedrock, limiter, kb_id, data_source_id, items)
    wait_and_verify_kb(bedrock, limiter, kb_id, data_source_id, items)
    print("[DONE] 13,010 筆有效現行法規已同步至 DynamoDB 與 Bedrock KB")


if __name__ == "__main__":
    try:
        main()
    except (ClientError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
