"""驗證資料已正確落地:DynamoDB 條文查詢 + KB-LAW/KB-CASE 檢索。任一步失敗 exit(1)。"""
import sys

import boto3

from config import DDB_LAW_TABLE, REGION, load_resources


def verify_dynamodb(ddb):
    table = ddb.Table(DDB_LAW_TABLE)
    resp = table.get_item(Key={"law_article": "訴願法#77"})
    item = resp.get("Item")
    if not item:
        raise RuntimeError(f"DynamoDB {DDB_LAW_TABLE} 查無 訴願法#77")
    text = item.get("text", "")
    print(f"[DynamoDB] 訴願法#77 條文前60字: {text[:60]}")
    print(f"[DynamoDB] 訴願法#77 修正日期: {item.get('amend_date', '')}")


def verify_kb_law(runtime, kb_id: str):
    resp = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": "訴願逾期不受理"},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": 3,
                "filter": {"notEquals": {"key": "law_type", "value": "普通法"}},
            }
        },
    )
    results = resp.get("retrievalResults", [])
    if not results:
        raise RuntimeError("KB-LAW retrieve 無結果")
    print("[KB-LAW] top3:")
    for r in results[:3]:
        meta = r.get("metadata", {})
        print(f"  law_name={meta.get('law_name')} article_no={meta.get('article_no')}")


def verify_kb_case(runtime, kb_id: str):
    resp = runtime.retrieve(
        knowledgeBaseId=kb_id,
        retrievalQuery={"text": "未於期限內提起訴願"},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": 3,
                "filter": {"equals": {"key": "result", "value": "不受理"}},
            }
        },
    )
    results = resp.get("retrievalResults", [])
    if not results:
        raise RuntimeError("KB-CASE retrieve 無結果")
    print("[KB-CASE] top3:")
    for r in results[:3]:
        meta = r.get("metadata", {})
        print(f"  case_no={meta.get('case_no')} section={meta.get('section')}")


def main():
    resources = load_resources()
    kb_law_id = resources.get("kb_law_id")
    kb_case_id = resources.get("kb_case_id")
    if not kb_law_id or not kb_case_id:
        print(
            "[ERROR] resources.json 缺少 kb_law_id/kb_case_id,請先執行 03_vectors_kb.py",
            file=sys.stderr,
        )
        sys.exit(1)

    ddb = boto3.resource("dynamodb", region_name=REGION)
    runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)

    verify_dynamodb(ddb)
    verify_kb_law(runtime, kb_law_id)
    verify_kb_case(runtime, kb_case_id)

    print("[DONE] 全部驗證步驟通過")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] 驗證失敗: {e}", file=sys.stderr)
        sys.exit(1)
