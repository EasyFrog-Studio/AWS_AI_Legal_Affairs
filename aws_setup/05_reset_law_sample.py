"""只重置法規 DynamoDB/Bedrock KB，並以固定種子匯入 10 筆可連至官方原文的法條。

安全條件：
- STS 帳號必須是 config.ACCOUNT_ID，region 固定為 config.REGION。
- 只允許操作 config.DDB_LAW_TABLE 與 resources.json 的 KB-LAW / data source。
- 必須傳入 ``--execute RESET-LAW-ONLY``；預設只列印樣本，不呼叫 AWS。
- 所有 Bedrock Agent / Runtime 請求共用節流器，間隔 1.1 秒，嚴格低於 1 RPS。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import random
import re
import sys
import time
import urllib.request
import zipfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

import boto3
from botocore.exceptions import ClientError

from config import (
    ACCOUNT_ID,
    DATASET_DIR,
    DDB_LAW_TABLE,
    KB_LAW_NAME,
    REGION,
    load_resources,
    save_resources,
)

# 語料位置與 config 的 CRAWL_* 同一套規則:預設在專案 data/ 下,未就位時以環境變數指向實際存放處
CRAWL_LAW_DIR = Path(
    os.environ.get("CRAWL_LAW_DIR", DATASET_DIR / "爬蟲集" / "chunk資料" / "法條")
)
SOURCE_LINK_CSV = Path(
    os.environ.get("SOURCE_LINK_CSV", DATASET_DIR / "爬蟲集" / "原文連結.csv")
)
OFFICIAL_INDEX_URLS = (
    "https://law.moj.gov.tw/api/Ch/Law/JSON",
    "https://law.moj.gov.tw/api/Ch/Order/JSON",
)
PCODE_RE = re.compile(r"[?&]pcode=([A-Z0-9]+)", re.IGNORECASE)
ARTICLE_RE = re.compile(r"\d+(?:-\d+)?\Z")
SAMPLE_SIZE = 10
RANDOM_SEED = 20260912
BEDROCK_MIN_INTERVAL_SECONDS = 1.1
KB_DELETE_BATCH_SIZE = 10
KB_WAIT_TIMEOUT_SECONDS = 900
LAW_ARTICLE_INDEX = "law-article-index"
EXECUTE_CONFIRMATION = "RESET-LAW-ONLY"


@dataclass
class RateLimiter:
    min_interval: float
    _last_call: float | None = None

    def call(self, fn: Callable[..., Any], /, **kwargs: Any) -> Any:
        now = time.monotonic()
        if self._last_call is not None:
            time.sleep(max(0.0, self.min_interval - (now - self._last_call)))
        result = fn(**kwargs)
        self._last_call = time.monotonic()
        return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        metavar="CONFIRMATION",
        help=f"真正重置 AWS 時必須給定精確字串 {EXECUTE_CONFIRMATION}",
    )
    return parser.parse_args()


def pcode_from_url(url: str) -> str:
    match = PCODE_RE.search(url or "")
    return match.group(1).upper() if match else ""


def load_csv_pcodes() -> dict[str, str]:
    if not SOURCE_LINK_CSV.exists():
        raise FileNotFoundError(f"找不到原文連結 CSV：{SOURCE_LINK_CSV}")
    mapping: dict[str, str] = {}
    with SOURCE_LINK_CSV.open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("類別", "").strip() != "法條":
                continue
            law_name = row.get("標題", "").strip()
            pcode = pcode_from_url(row.get("原文網址", "").strip())
            if law_name and pcode:
                mapping.setdefault(law_name, pcode)
    return mapping


def fetch_official_pcodes() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for url in OFFICIAL_INDEX_URLS:
        with urllib.request.urlopen(url, timeout=180) as response:
            payload = response.read()
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            json_name = next(name for name in archive.namelist() if name.endswith(".json"))
            data = json.loads(archive.read(json_name).decode("utf-8-sig"))
        laws = data["Laws"] if isinstance(data, dict) else data
        for law in laws:
            law_name = (law.get("LawName") or "").strip()
            pcode = pcode_from_url(law.get("LawURL") or "")
            if law_name and pcode:
                mapping.setdefault(law_name, pcode)
    return mapping


def load_candidates() -> tuple[list[dict[str, Any]], list[str]]:
    if not CRAWL_LAW_DIR.exists():
        raise FileNotFoundError(f"找不到法條來源目錄：{CRAWL_LAW_DIR}")

    pcodes = load_csv_pcodes()
    # CSV 是主要來源；官方 Open API 補齊 CSV 未涵蓋的中央法規/命令。
    for law_name, pcode in fetch_official_pcodes().items():
        pcodes.setdefault(law_name, pcode)

    candidates: list[dict[str, Any]] = []
    missing_link_laws: set[str] = set()
    seen: set[str] = set()
    for path in sorted(CRAWL_LAW_DIR.glob("法規-*.jsonl")):
        category_a = path.stem.removeprefix("法規-")
        with path.open(encoding="utf-8-sig") as stream:
            for line_no, line in enumerate(stream, 1):
                if not line.strip():
                    continue
                row = json.loads(line)
                meta = row.get("metadata") or {}
                law_name = str(meta.get("law_name") or "").strip()
                article_no = str(meta.get("article") or "").strip()
                text = str(row.get("內容") or "").strip()
                deleted = str(meta.get("deleted") or "false").strip().lower() == "true"
                if deleted or not law_name or not text:
                    continue
                if not ARTICLE_RE.fullmatch(article_no):
                    raise ValueError(f"{path.name}:{line_no} 非預期條號：{article_no!r}")
                law_article = f"{law_name}#{article_no}"
                if law_article in seen:
                    continue
                seen.add(law_article)
                pcode = pcodes.get(law_name, "")
                if not pcode:
                    missing_link_laws.add(law_name)
                law_page_url = (
                    f"https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode={pcode}"
                    if pcode
                    else ""
                )
                source_url = (
                    "https://law.moj.gov.tw/LawClass/LawSingle.aspx"
                    f"?pcode={pcode}&flno={article_no}"
                    if pcode
                    else ""
                )
                revised_date = str(meta.get("revised_date") or "").strip()
                candidates.append(
                    {
                        "law_article": law_article,
                        # 不寫進 DynamoDB；06 全量匯入用它排序並印來源分類涵蓋率
                        "category_a": category_a,
                        "law_name": law_name,
                        "article_no": article_no,
                        "article_label": f"第{article_no}條",
                        "text": text,
                        "revised_date": revised_date,
                        "amend_date": revised_date,
                        "is_current": True,
                        "source_system": "全國法規資料庫",
                        "pcode": pcode,
                        "law_page_url": law_page_url,
                        "source_url": source_url,
                        "source_file": path.name,
                    }
                )
    return candidates, sorted(missing_link_laws)


def choose_sample(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linkable = [item for item in candidates if item["source_url"]]
    if len(linkable) < SAMPLE_SIZE:
        raise RuntimeError(f"有官方連結的有效法條只有 {len(linkable)} 筆，不足 {SAMPLE_SIZE} 筆")
    sample = random.Random(RANDOM_SEED).sample(linkable, SAMPLE_SIZE)
    for law_id, item in enumerate(sample, 1):
        item["law_id"] = law_id
        item["document_id"] = f"LAW#{law_id:08d}"
    return sample


def print_sample(sample: list[dict[str, Any]], missing_link_laws: list[str]) -> None:
    print(
        json.dumps(
            [
                {
                    "law_id": item["law_id"],
                    "document_id": item["document_id"],
                    "law_name": item["law_name"],
                    "article_no": item["article_no"],
                    "revised_date": item["revised_date"],
                    "source_url": item["source_url"],
                }
                for item in sample
            ],
            ensure_ascii=False,
            indent=2,
        )
    )
    if missing_link_laws:
        print(
            f"[INFO] {len(missing_link_laws)} 部地方自治法規尚無中央 pcode，"
            "本次 10 筆格式樣本不從這些法規抽取："
            + "、".join(missing_link_laws)
        )


def assert_aws_scope(
    session: boto3.Session,
    resources: dict[str, Any],
    limiter: RateLimiter,
) -> tuple[str, str]:
    identity = session.client("sts").get_caller_identity()
    actual_account = identity["Account"]
    if actual_account != ACCOUNT_ID:
        raise RuntimeError(f"AWS 帳號不符：預期 {ACCOUNT_ID}，實際 {actual_account}")

    kb_id = resources.get("kb_law_id")
    data_source_id = resources.get("data_source_law_id")
    if not kb_id or not data_source_id:
        raise RuntimeError("resources.json 缺少 kb_law_id 或 data_source_law_id")

    ddb = session.client("dynamodb")
    try:
        table = ddb.describe_table(TableName=DDB_LAW_TABLE)["Table"]
        print(f"[VERIFY] DynamoDB={table['TableName']} status={table['TableStatus']}")
    except ddb.exceptions.ResourceNotFoundException:
        print(f"[VERIFY] DynamoDB={DDB_LAW_TABLE} 尚不存在，將建立")

    bedrock = session.client("bedrock-agent")
    kb = limiter.call(
        bedrock.get_knowledge_base,
        knowledgeBaseId=kb_id,
    )["knowledgeBase"]
    if kb["name"] != KB_LAW_NAME:
        raise RuntimeError(f"KB 名稱不符：預期 {KB_LAW_NAME}，實際 {kb['name']}")
    data_source = limiter.call(
        bedrock.get_data_source,
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
    )["dataSource"]
    if data_source["knowledgeBaseId"] != kb_id:
        raise RuntimeError("data source 不屬於指定 KB-LAW")
    print(
        f"[VERIFY] account={actual_account} region={REGION} "
        f"KB={kb['name']}({kb_id}) dataSource={data_source['name']}({data_source_id})"
    )
    return kb_id, data_source_id


def reset_dynamodb(session: boto3.Session) -> None:
    ddb = session.client("dynamodb")
    try:
        ddb.describe_table(TableName=DDB_LAW_TABLE)
        print(f"[RESET] 刪除法規表 {DDB_LAW_TABLE}")
        ddb.delete_table(TableName=DDB_LAW_TABLE)
        ddb.get_waiter("table_not_exists").wait(TableName=DDB_LAW_TABLE)
    except ddb.exceptions.ResourceNotFoundException:
        pass

    print(f"[RESET] 以 law_id(Number) 重建 {DDB_LAW_TABLE}")
    ddb.create_table(
        TableName=DDB_LAW_TABLE,
        AttributeDefinitions=[
            {"AttributeName": "law_id", "AttributeType": "N"},
            {"AttributeName": "law_article", "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": "law_id", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": LAW_ARTICLE_INDEX,
                "KeySchema": [{"AttributeName": "law_article", "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.get_waiter("table_exists").wait(TableName=DDB_LAW_TABLE)
    description = ddb.describe_table(TableName=DDB_LAW_TABLE)["Table"]
    if description["ItemCount"] != 0:
        raise RuntimeError("重建後 DynamoDB 並非空表")
    print("[VERIFY] DynamoDB 已清空且使用 law_id(Number) 主鍵")


def list_kb_documents(
    bedrock: Any, limiter: RateLimiter, kb_id: str, data_source_id: str
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "knowledgeBaseId": kb_id,
            "dataSourceId": data_source_id,
            "maxResults": 1000,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        response = limiter.call(bedrock.list_knowledge_base_documents, **kwargs)
        documents.extend(response.get("documentDetails", []))
        next_token = response.get("nextToken")
        if not next_token:
            return documents


def reset_kb(
    bedrock: Any, limiter: RateLimiter, kb_id: str, data_source_id: str
) -> None:
    documents = list_kb_documents(bedrock, limiter, kb_id, data_source_id)
    print(f"[RESET] KB-LAW 現有文件={len(documents)}")
    identifiers = [document["identifier"] for document in documents]
    for start in range(0, len(identifiers), KB_DELETE_BATCH_SIZE):
        batch = identifiers[start : start + KB_DELETE_BATCH_SIZE]
        limiter.call(
            bedrock.delete_knowledge_base_documents,
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            documentIdentifiers=batch,
        )
        print(f"  [DELETE] 已送出 {min(start + len(batch), len(identifiers))}/{len(identifiers)}")

    deadline = time.monotonic() + KB_WAIT_TIMEOUT_SECONDS
    while True:
        remaining = list_kb_documents(bedrock, limiter, kb_id, data_source_id)
        if not remaining:
            print("[VERIFY] KB-LAW 文件數=0")
            return
        if time.monotonic() >= deadline:
            statuses = sorted({item.get("status", "") for item in remaining})
            raise TimeoutError(f"等待 KB 清空逾時：remaining={len(remaining)} statuses={statuses}")
        print(f"  [WAIT] KB-LAW 尚有 {len(remaining)} 筆")
        time.sleep(5)


def recreate_kb_data_source(
    bedrock: Any,
    limiter: RateLimiter,
    kb_id: str,
    old_data_source_id: str,
) -> str:
    current = limiter.call(
        bedrock.get_data_source,
        knowledgeBaseId=kb_id,
        dataSourceId=old_data_source_id,
    )["dataSource"]
    if current.get("dataDeletionPolicy") != "DELETE":
        raise RuntimeError(
            f"拒絕刪除 data source：dataDeletionPolicy={current.get('dataDeletionPolicy')}"
        )
    name = current["name"]
    configuration = current["dataSourceConfiguration"]

    deadline = time.monotonic() + KB_WAIT_TIMEOUT_SECONDS
    while True:
        try:
            response = limiter.call(
                bedrock.delete_data_source,
                knowledgeBaseId=kb_id,
                dataSourceId=old_data_source_id,
            )
            break
        except ClientError as error:
            code = error.response.get("Error", {}).get("Code", "")
            message = error.response.get("Error", {}).get("Message", "")
            pending_document_request = (
                code == "ValidationException"
                and (
                    "IngestKnowledgeBaseDocument" in message
                    or "DeleteKnowledgeBaseDocument" in message
                )
            )
            if not pending_document_request or time.monotonic() >= deadline:
                raise
            print("  [WAIT] 先前的文件刪除仍在 Bedrock 背景執行，等待後重試")
            time.sleep(10)
    print(
        f"[RESET] 已送出 data source 刪除：{old_data_source_id} "
        f"status={response.get('status')}"
    )

    deadline = time.monotonic() + KB_WAIT_TIMEOUT_SECONDS
    while True:
        summaries = limiter.call(
            bedrock.list_data_sources,
            knowledgeBaseId=kb_id,
            maxResults=100,
        ).get("dataSourceSummaries", [])
        old = next(
            (item for item in summaries if item["dataSourceId"] == old_data_source_id),
            None,
        )
        if old is None:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待 data source {old_data_source_id} 刪除逾時")
        print(f"  [WAIT] data source 刪除狀態={old.get('status')}")
        time.sleep(5)

    created = limiter.call(
        bedrock.create_data_source,
        knowledgeBaseId=kb_id,
        name=name,
        dataSourceConfiguration=configuration,
        dataDeletionPolicy="DELETE",
    )["dataSource"]
    new_data_source_id = created["dataSourceId"]
    deadline = time.monotonic() + KB_WAIT_TIMEOUT_SECONDS
    print(
        f"[RESET] 已重建 data source：{new_data_source_id} "
        f"status={created.get('status')}"
    )

    while True:
        data_source = limiter.call(
            bedrock.get_data_source,
            knowledgeBaseId=kb_id,
            dataSourceId=new_data_source_id,
        )["dataSource"]
        status = data_source["status"]
        if status == "AVAILABLE":
            break
        if status == "FAILED":
            raise RuntimeError(
                f"data source 重建失敗：{data_source.get('failureReasons', [])}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待 data source {new_data_source_id} AVAILABLE 逾時")
        print(f"  [WAIT] 新 data source 狀態={status}")
        time.sleep(5)

    documents = list_kb_documents(
        bedrock,
        limiter,
        kb_id,
        new_data_source_id,
    )
    if documents:
        raise RuntimeError(f"重建後 data source 並非空白：{len(documents)} 筆")
    save_resources({"data_source_law_id": new_data_source_id})
    print("[VERIFY] 新 data source 文件數=0，resources.json 已更新")
    return new_data_source_id


DYNAMODB_FIELDS = (
    "law_id",
    "law_article",
    "law_name",
    "article_no",
    "text",
    "revised_date",
    "is_current",
    "source_system",
    "pcode",
    "law_page_url",
    "source_url",
)


def write_dynamodb(session: boto3.Session, sample: list[dict[str, Any]]) -> None:
    table = session.resource("dynamodb").Table(DDB_LAW_TABLE)
    with table.batch_writer(overwrite_by_pkeys=["law_id"]) as batch:
        for source in sample:
            item = {key: source[key] for key in DYNAMODB_FIELDS}
            item["law_id"] = Decimal(source["law_id"])
            batch.put_item(Item=item)
    print(f"[INGEST] DynamoDB 寫入 {len(sample)} 筆")


def kb_document(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": {
            "dataSourceType": "CUSTOM",
            "custom": {
                "customDocumentIdentifier": {"id": item["document_id"]},
                "sourceType": "IN_LINE",
                "inlineContent": {
                    "type": "TEXT",
                    "textContent": {"data": item["text"]},
                },
            },
        },
        "metadata": {
            "type": "IN_LINE_ATTRIBUTE",
            "inlineAttributes": [
                {
                    "key": "law_id",
                    "value": {"type": "STRING", "stringValue": str(item["law_id"])},
                }
            ],
        },
    }


def ingest_kb(
    bedrock: Any,
    limiter: RateLimiter,
    kb_id: str,
    data_source_id: str,
    sample: list[dict[str, Any]],
) -> None:
    documents = [kb_document(item) for item in sample]
    limiter.call(
        bedrock.ingest_knowledge_base_documents,
        knowledgeBaseId=kb_id,
        dataSourceId=data_source_id,
        documents=documents,
    )
    identifiers = [document["content"]["custom"]["customDocumentIdentifier"] for document in documents]
    deadline = time.monotonic() + KB_WAIT_TIMEOUT_SECONDS
    while True:
        details = limiter.call(
            bedrock.get_knowledge_base_documents,
            knowledgeBaseId=kb_id,
            dataSourceId=data_source_id,
            documentIdentifiers=[
                {"dataSourceType": "CUSTOM", "custom": identifier} for identifier in identifiers
            ],
        ).get("documentDetails", [])
        statuses = {detail["identifier"]["custom"]["id"]: detail["status"] for detail in details}
        failed = {doc_id: status for doc_id, status in statuses.items() if "FAILED" in status}
        if failed:
            raise RuntimeError(f"KB 文件建立失敗：{failed}")
        if len(statuses) == len(sample) and all(status == "INDEXED" for status in statuses.values()):
            print(f"[INGEST] KB-LAW INDEXED={len(statuses)}")
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(f"等待 KB INDEXED 逾時：{statuses}")
        print(f"  [WAIT] KB statuses={statuses}")
        time.sleep(5)


def verify_dynamodb(session: boto3.Session, sample: list[dict[str, Any]]) -> None:
    table = session.resource("dynamodb").Table(DDB_LAW_TABLE)
    response = table.scan(ConsistentRead=True)
    items = response.get("Items", [])
    while response.get("LastEvaluatedKey"):
        response = table.scan(
            ConsistentRead=True, ExclusiveStartKey=response["LastEvaluatedKey"]
        )
        items.extend(response.get("Items", []))
    expected = {Decimal(item["law_id"]): item for item in sample}
    actual = {item["law_id"]: item for item in items}
    if set(actual) != set(expected) or len(actual) != SAMPLE_SIZE:
        raise RuntimeError(f"DynamoDB 驗證失敗：expected IDs={set(expected)} actual IDs={set(actual)}")
    for law_id, source in expected.items():
        item = actual[law_id]
        for key in ("law_article", "article_no", "law_name", "text", "source_url"):
            if item.get(key) != source[key]:
                raise RuntimeError(f"DynamoDB law_id={law_id} 欄位 {key} 不一致")
    print("[VERIFY] DynamoDB 10 筆 ID、法規名稱、條號、全文與官方連結一致")


def verify_kb(
    bedrock: Any,
    limiter: RateLimiter,
    kb_id: str,
    data_source_id: str,
    sample: list[dict[str, Any]],
) -> None:
    documents = list_kb_documents(bedrock, limiter, kb_id, data_source_id)
    actual_ids = {
        item["identifier"].get("custom", {}).get("id")
        for item in documents
        if item["identifier"].get("dataSourceType") == "CUSTOM"
    }
    expected_ids = {item["document_id"] for item in sample}
    if actual_ids != expected_ids or len(documents) != SAMPLE_SIZE:
        raise RuntimeError(f"KB 文件驗證失敗：expected={expected_ids} actual={actual_ids}")
    if any(item.get("status") != "INDEXED" for item in documents):
        raise RuntimeError(f"KB 尚未全部 INDEXED：{documents}")
    print("[VERIFY] KB-LAW 只有指定 10 筆且全部 INDEXED")


def main() -> None:
    args = parse_args()
    candidates, missing_link_laws = load_candidates()
    sample = choose_sample(candidates)
    print_sample(sample, missing_link_laws)

    if args.execute is None:
        print(f"[DRY-RUN] 未呼叫 AWS；要執行須傳 --execute {EXECUTE_CONFIRMATION}")
        return
    if args.execute != EXECUTE_CONFIRMATION:
        raise SystemExit(f"確認字串錯誤；必須是 {EXECUTE_CONFIRMATION}")

    session = boto3.Session(region_name=REGION)
    resources = load_resources()
    limiter = RateLimiter(BEDROCK_MIN_INTERVAL_SECONDS)
    kb_id, data_source_id = assert_aws_scope(session, resources, limiter)
    bedrock = session.client("bedrock-agent")

    data_source_id = recreate_kb_data_source(
        bedrock,
        limiter,
        kb_id,
        data_source_id,
    )
    reset_dynamodb(session)
    write_dynamodb(session, sample)
    ingest_kb(bedrock, limiter, kb_id, data_source_id, sample)
    verify_dynamodb(session, sample)
    verify_kb(bedrock, limiter, kb_id, data_source_id, sample)
    print("[DONE] 僅法規 DynamoDB/KB 已重置並匯入同一批 10 筆")


if __name__ == "__main__":
    try:
        main()
    except (ClientError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
