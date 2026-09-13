"""驗收腳本:實際打 AWS 讀取端,證明三類參考見解的 KB 與 DynamoDB 可用且互相對得起來。任一項失敗 exit(1)。"""
import argparse
import sys

import boto3
from botocore.exceptions import ClientError

from config import REFERENCE_DDB_PK, REFERENCE_KINDS, REGION, S3_BUCKET, load_resources

# doc_kind -> resources.json 存放 KB id 的 key 名
_KB_ID_KEY = {
    "司法院釋字": "kb_interpretation_id",
    "行政函釋": "kb_ruling_id",
    "行政法院裁判": "kb_judgment_id",
}
# 灌完資料後每一類應有的 DynamoDB 筆數(一份文件一筆,非 chunk 數)
_EXPECTED_DDB_COUNT = {
    "司法院釋字": 811,
    "行政函釋": 800,
    "行政法院裁判": 775,
}
# 每一類語意檢索用的查詢字串,貼合該類文件的典型主題以求能命中
_RETRIEVAL_QUERY = {
    "司法院釋字": "訴願人不服行政處分之救濟權利",
    "行政函釋": "寄存送達生效日期之計算",
    "行政法院裁判": "行政處分之定義與救濟",
}

_PASSED: list[str] = []
_FAILED: list[str] = []


def _report(ok: bool, label: str, detail: str) -> None:
    print(f"{'[PASS]' if ok else '[FAIL]'} {label}: {detail}")
    (_PASSED if ok else _FAILED).append(label)


def check_kb_active(agent, kb_id: str, doc_kind: str) -> None:
    """檢查 a:KB 存在且狀態為 ACTIVE。"""
    label = f"{doc_kind} KB 存在且 ACTIVE"
    try:
        resp = agent.get_knowledge_base(knowledgeBaseId=kb_id)
        status = resp["knowledgeBase"]["status"]
        _report(status == "ACTIVE", label, f"kb_id={kb_id} status={status}")
    except ClientError as e:
        _report(False, label, str(e))


def check_ddb_count(ddb, table_name: str, doc_kind: str) -> None:
    """檢查 b:表存在且筆數符合;ItemCount 是約每 6 小時更新的近似值,故用 scan+COUNT 取精確筆數。"""
    label = f"{doc_kind} DynamoDB 表存在且筆數符合"
    expected = _EXPECTED_DDB_COUNT[doc_kind]
    try:
        table = ddb.Table(table_name)
        count = 0
        scan_kwargs: dict = {"Select": "COUNT"}
        while True:
            resp = table.scan(**scan_kwargs)
            count += resp["Count"]
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
            scan_kwargs["ExclusiveStartKey"] = last_key
        _report(
            count == expected,
            label,
            f"table={table_name} 精確筆數(scan+COUNT)={count} 預期={expected}",
        )
    except ClientError as e:
        _report(False, label, str(e))


def check_retrieve(bart, kb_id: str, doc_kind: str) -> list[dict]:
    """檢查 c:語意檢索可用,回傳原始 retrievalResults 供後續檢查 d 與跨類污染檢查沿用。"""
    label = f"{doc_kind} 語意檢索可用"
    query = _RETRIEVAL_QUERY[doc_kind]
    try:
        resp = bart.retrieve(
            knowledgeBaseId=kb_id,
            retrievalQuery={"text": query},
            retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": 3}},
        )
        results = resp.get("retrievalResults", [])
        _report(len(results) >= 1, label, f"query=「{query}」命中 {len(results)} 筆")
        for r in results:
            meta = r.get("metadata", {})
            text = r.get("content", {}).get("text", "")
            print(f"    ref_id={meta.get('ref_id')} doc_kind={meta.get('doc_kind')} content={text[:40]}")
        return results
    except ClientError as e:
        _report(False, label, str(e))
        return []


def check_kb_ddb_consistency(ddb, table_name: str, doc_kind: str, results: list[dict]) -> list[dict]:
    """檢查 d(核心):KB 只存編號,詳細資料靠 ref_id 回 DynamoDB 精查,故檢索到的每個 ref_id 都必須在表裡查得到且 doc_kind 相符。"""
    label = f"{doc_kind} KB 檢索結果與 DynamoDB 對得起來"
    if not results:
        _report(False, label, "無檢索結果可驗證(檢查 c 未命中)")
        return []
    try:
        table = ddb.Table(table_name)
        items: list[dict] = []
        all_ok = True
        for r in results:
            ref_id = r.get("metadata", {}).get("ref_id")
            item = table.get_item(Key={REFERENCE_DDB_PK: ref_id}).get("Item")
            if not item:
                all_ok = False
                print(f"    [FAIL] ref_id={ref_id} 在 {table_name} 查無 item")
                continue
            if item.get("doc_kind") != doc_kind:
                all_ok = False
                print(f"    [FAIL] ref_id={ref_id} item.doc_kind={item.get('doc_kind')} 應為 {doc_kind}")
                continue
            items.append(item)
            print(f"    [OK] ref_id={ref_id} -> DynamoDB name={item.get('name')} doc_kind={item.get('doc_kind')}")
        _report(all_ok, label, f"{len(items)}/{len(results)} 筆 ref_id 皆查得且 doc_kind 相符")
        return items
    except ClientError as e:
        _report(False, label, str(e))
        return []


def check_s3_pdf(s3, table_name: str, doc_kind: str, items: list[dict]) -> None:
    """檢查 e:DynamoDB item 帶出的 s3_key 對應的原始 PDF 確實存在於 S3。"""
    label = f"{doc_kind} S3 原始 PDF 存在"
    if not items:
        _report(False, label, f"無 DynamoDB item 可驗證(檢查 d 未查得,table={table_name})")
        return
    try:
        all_ok = True
        for item in items:
            s3_key = item.get("s3_key", "")
            try:
                s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
                print(f"    [OK] s3://{S3_BUCKET}/{s3_key}")
            except ClientError as e:
                all_ok = False
                print(f"    [FAIL] s3://{S3_BUCKET}/{s3_key}: {e}")
        _report(all_ok, label, f"{len(items)} 筆 s3_key" + ("全數存在" if all_ok else ",部分缺失見上列"))
    except ClientError as e:
        _report(False, label, str(e))


def check_no_cross_contamination(doc_kind: str, results: list[dict]) -> None:
    """額外跨類檢查:三個 KB 共用同一個 vector bucket,檢索結果的 doc_kind 不應混進其他兩類。"""
    label = f"{doc_kind} 檢索結果無跨類污染"
    if not results:
        _report(False, label, "無檢索結果可驗證(檢查 c 未命中)")
        return
    polluted = [
        r.get("metadata", {}).get("ref_id")
        for r in results
        if r.get("metadata", {}).get("doc_kind") != doc_kind
    ]
    _report(not polluted, label, "全數 doc_kind 相符" if not polluted else f"污染 ref_id={polluted}")


def main():
    parser = argparse.ArgumentParser(description="驗收參考見解三類 KB 與 DynamoDB 上雲結果")
    parser.add_argument("--only", choices=[k[0] for k in REFERENCE_KINDS], help="只驗這一類")
    args = parser.parse_args()

    resources = load_resources()
    agent = boto3.client("bedrock-agent", region_name=REGION)
    bart = boto3.client("bedrock-agent-runtime", region_name=REGION)
    ddb = boto3.resource("dynamodb", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    kinds = REFERENCE_KINDS if not args.only else tuple(k for k in REFERENCE_KINDS if k[0] == args.only)

    retrieval_results: dict[str, list[dict]] = {}
    for doc_kind, kb_name, index_name, ddb_table in kinds:
        print(f"\n===== {doc_kind}({kb_name} / {index_name} / {ddb_table}) =====")
        kb_id = resources.get(_KB_ID_KEY[doc_kind])
        if not kb_id:
            _report(False, f"{doc_kind} KB 存在且 ACTIVE", f"resources.json 缺少 {_KB_ID_KEY[doc_kind]}")
            retrieval_results[doc_kind] = []
            continue
        check_kb_active(agent, kb_id, doc_kind)
        check_ddb_count(ddb, ddb_table, doc_kind)
        results = check_retrieve(bart, kb_id, doc_kind)
        retrieval_results[doc_kind] = results
        items = check_kb_ddb_consistency(ddb, ddb_table, doc_kind, results)
        check_s3_pdf(s3, ddb_table, doc_kind, items)

    print("\n===== 跨類污染檢查 =====")
    for doc_kind, results in retrieval_results.items():
        check_no_cross_contamination(doc_kind, results)

    print(f"\n[總結] 通過 {len(_PASSED)} 項 / 失敗 {len(_FAILED)} 項")
    if _FAILED:
        print(f"[總結] 失敗項目: {_FAILED}")
        sys.exit(1)
    print("[DONE] 全部驗證項目通過")


if __name__ == "__main__":
    try:
        main()
    except ClientError as e:
        print(f"[ERROR] AWS 呼叫失敗: {e}", file=sys.stderr)
        sys.exit(1)
