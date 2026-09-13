"""驗證資料已正確落地:DynamoDB 條文查詢 + KB-LAW 檢索 + KB-CASE 檢索並回 DynamoDB 精查。任一步失敗 exit(1)。"""
import sys

import boto3

from config import DDB_LAW_TABLE, DDB_PAST_DECISIONS_TABLE, REGION, load_resources


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


def verify_kb_case(runtime, ddb, kb_id: str):
    """KB 只存 case_id,詳細欄位在 DynamoDB:兩層接得起來才算過,
    只印 KB metadata 的話,精查那一半壞掉時這支腳本照樣印綠燈。"""
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
    table = ddb.Table(DDB_PAST_DECISIONS_TABLE)
    print("[KB-CASE] top3:")
    for r in results[:3]:
        case_id = (r.get("metadata") or {}).get("case_id")
        if not case_id:
            raise RuntimeError(f"KB-CASE chunk 缺 case_id,無法回 DynamoDB 精查: {r.get('metadata')}")
        item = table.get_item(Key={"case_id": case_id}).get("Item")
        if not item:
            raise RuntimeError(f"{DDB_PAST_DECISIONS_TABLE} 查無 {case_id}")
        if item.get("result") != "不受理":
            raise RuntimeError(f"{case_id} 的 result 與檢索 filter 不符: {item.get('result')}")
        print(f"  case_id={case_id} case_no={item.get('case_no')} 案型={item.get('case_type')} 原文={item.get('source_url') or item.get('source_file')}")


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
    verify_kb_case(runtime, ddb, kb_case_id)

    print("[DONE] 全部驗證步驟通過")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"[ERROR] 驗證失敗: {e}", file=sys.stderr)
        sys.exit(1)
