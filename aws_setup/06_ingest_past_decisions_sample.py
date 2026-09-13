"""固定抽樣 10 個過往案例，建立 DynamoDB 主檔並準備最小化 KB 文件。

預設只做離線 dry-run；只有傳入 ``--execute IMPORT-10-PAST-DECISIONS`` 才會：
1. 核對 AWS account/region。
2. 建立 ``appeal_past_decisions``（case_id String partition key）。
3. 以 PutItem 寫入同一組 10 個完整案件，再逐筆 GetItem 驗證。

KB 文件只輸出成 manifest，不直接呼叫 IngestKnowledgeBaseDocuments。現有 data source
是 CUSTOM，但競賽允許清單未列該 API；不得以 StartIngestionJob 取代，因 CUSTOM
資料來源沒有可同步的外部 source。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from config import ACCOUNT_ID, CRAWL_CASE_CHUNK_DIR, REGION

TABLE_NAME = "appeal_past_decisions"
SAMPLE_SIZE = 10
RANDOM_SEED = 20260912
HOLDOUT_YEARS = frozenset({"114"})
MAX_SAFE_ITEM_BYTES = 350 * 1024
EXECUTE_CONFIRMATION = "IMPORT-10-PAST-DECISIONS"

CRAWL_CASE_DIR = CRAWL_CASE_CHUNK_DIR
OUTPUT_DIR = Path(__file__).resolve().parents[2] / "data" / "output"
DDB_MANIFEST_PATH = OUTPUT_DIR / "past_decisions_sample_10_dynamodb.json"
KB_MANIFEST_PATH = OUTPUT_DIR / "past_decisions_sample_10_kb.json"
REPORT_PATH = OUTPUT_DIR / "past_decisions_sample_10_report.json"

_CHUNK_INDEX_RE = re.compile(r"-(\d{3})$")
_SPLIT_LAWS_RE = re.compile(r"[；;]\s*")

# 競賽規範要求上雲前排除個資、財務/付款、敏感屬性及惡意資料。
# 這些規則採保守策略；命中者完全不進可抽樣母體。
SCREENING_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("taiwan_national_id", re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?!\d)")),
    ("email", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")),
    ("mobile_phone", re.compile(r"(?<!\d)09\d{2}[- ]?\d{3}[- ]?\d{3}(?!\d)")),
    ("credit_card", re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")),
    ("bank_or_payment_account", re.compile(r"(?:銀行帳號|金融帳號|信用卡號|存摺帳號|付款帳號).{0,24}\d{5,}")),
    ("explicit_address", re.compile(r"(?:戶籍地址|居住地址|通訊地址|住址)\s*[：:]")),
    ("medical_sensitive", re.compile(r"(?:病歷|診斷證明|身心障礙|精神疾病|罹患|住院|就醫|醫院診療)")),
    ("identity_number", re.compile(r"(?:身分證字號|國民身分證|統一編號).{0,24}[A-Z0-9]{6,}")),
    ("malicious_payload", re.compile(r"(?:<script\b|javascript:|ignore previous instructions|忽略先前指示)", re.I)),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        metavar="CONFIRMATION",
        help=f"真正建立/寫入 DynamoDB 必須傳入 {EXECUTE_CONFIRMATION}",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} JSON 解析失敗: {exc}") from exc
    return rows


def chunk_index(row: dict[str, Any]) -> int:
    match = _CHUNK_INDEX_RE.search(str(row.get("片段名", "")))
    if not match:
        raise ValueError(f"片段名缺少三位數索引: {row.get('片段名')}")
    return int(match.group(1))


def parse_related_laws(raw: Any) -> list[str]:
    return [value.strip() for value in _SPLIT_LAWS_RE.split(str(raw or "")) if value.strip()]


def estimate_item_bytes(item: dict[str, Any]) -> int:
    # JSON UTF-8 大小略高於大多數此 schema 的 DynamoDB attribute size，並另設 50 KiB 緩衝。
    return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


def screening_findings(text: str) -> list[str]:
    return [name for name, pattern in SCREENING_RULES if pattern.search(text)]


def collect_cases() -> tuple[dict[str, dict[str, Any]], dict[str, int]]:
    if not CRAWL_CASE_DIR.is_dir():
        raise FileNotFoundError(f"找不到案例資料夾: {CRAWL_CASE_DIR}")

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats = {"source_rows": 0, "holdout_rows": 0}
    for path in sorted(CRAWL_CASE_DIR.glob("*.jsonl")):
        for row in load_jsonl(path):
            stats["source_rows"] += 1
            metadata = row.get("metadata", {})
            if str(metadata.get("year", "")) in HOLDOUT_YEARS:
                stats["holdout_rows"] += 1
                continue
            source_file = str(metadata.get("source_file") or row.get("來源檔") or "").strip()
            if not source_file:
                raise ValueError(f"案例缺少 source_file: {row.get('片段名')}")
            grouped[source_file].append(row)

    cases: dict[str, dict[str, Any]] = {}
    for source_file, rows in grouped.items():
        rows.sort(key=lambda row: (str(row.get("metadata", {}).get("section", "")), chunk_index(row)))
        first_meta = rows[0].get("metadata", {})
        case_id = source_file
        sections: dict[str, list[dict[str, Any]]] = defaultdict(list)
        related_laws: set[str] = set()
        for row in rows:
            meta = row.get("metadata", {})
            if str(meta.get("source_file", source_file)) != source_file:
                raise ValueError(f"同一聚合案件的 source_file 不一致: {source_file}")
            section = str(meta.get("section", ""))
            if section in {"事實", "理由"}:
                sections[section].append(row)
            related_laws.update(parse_related_laws(meta.get("related_laws")))

        if not sections["事實"] or not sections["理由"]:
            continue

        for section_rows in sections.values():
            section_rows.sort(key=chunk_index)

        facts = "\n\n".join(str(row.get("內容", "")).strip() for row in sections["事實"] if str(row.get("內容", "")).strip())
        reasoning = "\n\n".join(str(row.get("內容", "")).strip() for row in sections["理由"] if str(row.get("內容", "")).strip())
        if not facts or not reasoning:
            continue

        item = {
            "case_id": case_id,
            "case_no": str(first_meta.get("case_no", "")),
            "year": str(first_meta.get("year", "")),
            "result": str(first_meta.get("result", "")),
            "related_laws": sorted(related_laws),
            "facts": facts,
            "reasoning": reasoning,
            "source_file": source_file,
            "source_url": str(first_meta.get("source_url", "")),
            "screening_status": "passed_local_screening_v1",
            "schema_version": 1,
        }
        item_bytes = estimate_item_bytes(item)
        findings = screening_findings(f"{facts}\n{reasoning}")
        cases[case_id] = {
            "item": item,
            "item_bytes": item_bytes,
            "findings": findings,
            "rows": sections["事實"] + sections["理由"],
        }

    stats["eligible_grouped_cases"] = len(grouped)
    stats["complete_cases"] = len(cases)
    return cases, stats


def choose_sample(cases: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    safe_ids = sorted(
        case_id
        for case_id, case in cases.items()
        if not case["findings"] and case["item_bytes"] <= MAX_SAFE_ITEM_BYTES
    )
    if len(safe_ids) < SAMPLE_SIZE:
        raise ValueError(f"安全且完整的案例只有 {len(safe_ids)} 案，不足 {SAMPLE_SIZE} 案")
    selected_ids = random.Random(RANDOM_SEED).sample(safe_ids, SAMPLE_SIZE)
    return [cases[case_id] for case_id in selected_ids]


def build_kb_documents(sample: list[dict[str, Any]]) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for case in sample:
        case_id = case["item"]["case_id"]
        for row in case["rows"]:
            chunk_id = str(row["片段名"])
            if chunk_id in seen_ids:
                raise ValueError(f"KB chunk_id 重複: {chunk_id}")
            seen_ids.add(chunk_id)
            section = str(row.get("metadata", {}).get("section", ""))
            documents.append(
                {
                    "id": chunk_id,
                    "case_id": case_id,
                    "section": section,
                    "content": str(row.get("內容", "")).strip(),
                }
            )
    return documents


def write_manifests(sample: list[dict[str, Any]], kb_documents: list[dict[str, Any]], stats: dict[str, int]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    items = [case["item"] for case in sample]
    report = {
        "seed": RANDOM_SEED,
        "sample_size": SAMPLE_SIZE,
        "holdout_years": sorted(HOLDOUT_YEARS),
        "source_stats": stats,
        "kb_document_count": len(kb_documents),
        "cases": [
            {
                "case_id": case["item"]["case_id"],
                "case_no": case["item"]["case_no"],
                "year": case["item"]["year"],
                "result": case["item"]["result"],
                "related_law_count": len(case["item"]["related_laws"]),
                "item_bytes": case["item_bytes"],
                "within_350_kib": case["item_bytes"] <= MAX_SAFE_ITEM_BYTES,
                "screening_findings": case["findings"],
                "kb_chunk_count": len(case["rows"]),
            }
            for case in sample
        ],
    }
    DDB_MANIFEST_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    KB_MANIFEST_PATH.write_text(json.dumps(kb_documents, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def assert_sample(sample: list[dict[str, Any]], kb_documents: list[dict[str, Any]]) -> None:
    items = [case["item"] for case in sample]
    case_ids = [item["case_id"] for item in items]
    if len(items) != SAMPLE_SIZE or len(set(case_ids)) != SAMPLE_SIZE:
        raise ValueError("樣本必須是 10 個唯一案件")
    if any(item["year"] in HOLDOUT_YEARS for item in items):
        raise ValueError("樣本含 114 年留出案例")
    if any(not item["source_url"].startswith("https://web.law.ntpc.gov.tw/") for item in items):
        raise ValueError("樣本含缺失或非新北市政府官方網域的 source_url")
    if any(case["findings"] for case in sample):
        raise ValueError("樣本含敏感資料規則命中")
    if any(case["item_bytes"] > MAX_SAFE_ITEM_BYTES for case in sample):
        raise ValueError("樣本含超過 350 KiB 的 DynamoDB item")
    if {document["case_id"] for document in kb_documents} != set(case_ids):
        raise ValueError("KB 與 DynamoDB 的 case_id 集合不一致")
    if any(document["section"] not in {"事實", "理由"} for document in kb_documents):
        raise ValueError("KB 含非事實/理由 section")


def assert_aws_scope(session: boto3.Session) -> None:
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != ACCOUNT_ID:
        raise RuntimeError(f"AWS account 不符: expected={ACCOUNT_ID}, actual={identity.get('Account')}")
    if session.region_name != REGION:
        raise RuntimeError(f"AWS region 不符: expected={REGION}, actual={session.region_name}")


def ensure_table(session: boto3.Session) -> None:
    client = session.client("dynamodb")
    try:
        table = client.describe_table(TableName=TABLE_NAME)["Table"]
        if table.get("KeySchema") != [{"AttributeName": "case_id", "KeyType": "HASH"}]:
            raise RuntimeError(f"既有 {TABLE_NAME} key schema 不符，不覆寫")
        if table.get("TableStatus") != "ACTIVE":
            raise RuntimeError(f"既有 {TABLE_NAME} 非 ACTIVE: {table.get('TableStatus')}")
        print(f"[SKIP] DynamoDB 表已存在且 schema 正確: {TABLE_NAME}")
        return
    except client.exceptions.ResourceNotFoundException:
        pass

    client.create_table(
        TableName=TABLE_NAME,
        AttributeDefinitions=[{"AttributeName": "case_id", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "case_id", "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    client.get_waiter("table_exists").wait(TableName=TABLE_NAME)
    print(f"[CREATE] DynamoDB 表建立完成: {TABLE_NAME}")


def write_and_verify_dynamodb(session: boto3.Session, sample: list[dict[str, Any]]) -> None:
    table = session.resource("dynamodb").Table(TABLE_NAME)
    for case in sample:
        item = dict(case["item"])
        item["ingested_at"] = datetime.now(timezone.utc).isoformat()
        table.put_item(Item=item)
        print(f"[PUT] {item['case_id']} ({case['item_bytes']} bytes)")

    for case in sample:
        case_id = case["item"]["case_id"]
        response = table.get_item(Key={"case_id": case_id}, ConsistentRead=True)
        item = response.get("Item")
        if not item or item.get("case_id") != case_id:
            raise RuntimeError(f"DynamoDB 驗證失敗: {case_id}")
        for required in ("case_no", "year", "result", "related_laws", "facts", "reasoning", "source_url"):
            if required not in item:
                raise RuntimeError(f"DynamoDB {case_id} 缺少欄位: {required}")
        if not item["source_url"].startswith("https://web.law.ntpc.gov.tw/"):
            raise RuntimeError(f"DynamoDB {case_id} 的 source_url 非官方網域")
    print(f"[VERIFY] DynamoDB 逐筆 GetItem 驗證完成: {len(sample)} 案")


def print_summary(sample: list[dict[str, Any]], kb_documents: list[dict[str, Any]], stats: dict[str, int]) -> None:
    print(
        f"[DRY-RUN] source_rows={stats['source_rows']} holdout_rows={stats['holdout_rows']} "
        f"complete_cases={stats['complete_cases']} seed={RANDOM_SEED}"
    )
    for index, case in enumerate(sample, start=1):
        item = case["item"]
        print(
            f"  {index:02d}. {item['case_id']} year={item['year']} result={item['result']} "
            f"item_bytes={case['item_bytes']} kb_chunks={len(case['rows'])} findings=0"
        )
    print(f"[MANIFEST] DynamoDB={DDB_MANIFEST_PATH}")
    print(f"[MANIFEST] KB={KB_MANIFEST_PATH} ({len(kb_documents)} chunks)")
    print(f"[REPORT] {REPORT_PATH}")


def main() -> None:
    args = parse_args()
    cases, stats = collect_cases()
    sample = choose_sample(cases)
    kb_documents = build_kb_documents(sample)
    assert_sample(sample, kb_documents)
    write_manifests(sample, kb_documents, stats)
    print_summary(sample, kb_documents, stats)

    if args.execute is None:
        print(f"[DRY-RUN] 未呼叫 AWS；要建立/寫入 DynamoDB 請傳 --execute {EXECUTE_CONFIRMATION}")
        return
    if args.execute != EXECUTE_CONFIRMATION:
        raise SystemExit(f"確認字串錯誤；必須是 {EXECUTE_CONFIRMATION}")

    session = boto3.Session(region_name=REGION)
    assert_aws_scope(session)
    ensure_table(session)
    write_and_verify_dynamodb(session, sample)
    print("[DONE] appeal_past_decisions 已建立並匯入固定 10 案；KB manifest 已準備，未呼叫未允許的 CUSTOM ingestion API")


if __name__ == "__main__":
    try:
        main()
    except (ClientError, OSError, ValueError, RuntimeError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)
