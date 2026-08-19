"""建立 DynamoDB 表(on-demand)並從 law_chunks.jsonl 匯入 appeal_law_articles。
可重跑:表已存在跳過建立;批次寫入本身即冪等(相同 PK 覆寫)。
"""
import json
import sys

import boto3
from botocore.exceptions import ClientError

from config import DDB_CASE_TABLE, DDB_LAW_TABLE, LAW_CHUNKS_PATH, REGION


def ensure_table(ddb, table_name: str, pk_name: str):
    try:
        ddb.describe_table(TableName=table_name)
        print(f"[SKIP] 表已存在: {table_name}")
        return
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") != "ResourceNotFoundException":
            raise

    ddb.create_table(
        TableName=table_name,
        AttributeDefinitions=[{"AttributeName": pk_name, "AttributeType": "S"}],
        KeySchema=[{"AttributeName": pk_name, "KeyType": "HASH"}],
        BillingMode="PAY_PER_REQUEST",
    )
    waiter = ddb.get_waiter("table_exists")
    waiter.wait(TableName=table_name)
    print(f"[CREATE] 表建立完成: {table_name}")


def load_law_articles(resource, table_name: str) -> int:
    if not LAW_CHUNKS_PATH.exists():
        print(f"[SKIP] 找不到 {LAW_CHUNKS_PATH},略過資料匯入")
        return 0

    table = resource.Table(table_name)
    count = 0
    with LAW_CHUNKS_PATH.open("r", encoding="utf-8") as f, table.batch_writer() as batch:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            meta = row.get("metadata", {})
            item = {
                "law_article": row["id"],
                "law_name": meta.get("law_name", ""),
                "article_no": meta.get("article_no", ""),
                "text": row.get("text", ""),
                "amend_date": meta.get("amend_date", ""),
                "chapter": meta.get("chapter", ""),
                "law_type": meta.get("law_type", ""),
            }
            batch.put_item(Item=item)
            count += 1
    return count


def main():
    ddb = boto3.client("dynamodb", region_name=REGION)
    resource = boto3.resource("dynamodb", region_name=REGION)

    ensure_table(ddb, DDB_LAW_TABLE, "law_article")
    ensure_table(ddb, DDB_CASE_TABLE, "case_id")

    count = load_law_articles(resource, DDB_LAW_TABLE)

    print(f"[DONE] {DDB_LAW_TABLE} 匯入筆數={count};{DDB_CASE_TABLE} 已建立(無此腳本匯入資料)")


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
