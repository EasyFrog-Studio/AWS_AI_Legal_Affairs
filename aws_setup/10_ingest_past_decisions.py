"""讀過往訴願決定書 chunk(爬蟲語料 + 官方語料),灌 KB-CASE 與 appeal_past_decisions。

KB 只放檢索必要的:段落原文、case_id 與四個過濾鍵。法規引述與結語兩種段落不灌——
同一部法的上千件案子那兩段幾乎逐字相同,沒有區別力,只會把 15 個檢索名額佔滿。
DynamoDB 只放總覽欄位,不存決定書全文:承辦人要看全文點 source_url 回官方查詢系統。

預設只做離線 dry-run;帶 --execute IMPORT-PAST-DECISIONS 才會核對 AWS account/region、
同步 KB-CASE 的文件、灌 KB、寫 DynamoDB,最後抽驗。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path

import boto3

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_sibling(module_name: str, filename: str):
    """數字開頭的腳本檔名不是合法模組名,只能以路徑載入。"""
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


base = _load_sibling("law_sample_reset", "05_reset_law_sample.py")
refing = _load_sibling("reference_ingest", "08_reference_ingest.py")

from config import (  # noqa: E402
    ACCOUNT_ID,
    CASE_CHUNKS_PATH,
    CRAWL_CASE_CHUNK_DIR,
    DDB_PAST_DECISIONS_TABLE,
    INGEST_FAILURES_PATH,
    REGION,
    load_resources,
)

HOLDOUT_YEARS = frozenset({"114"})
# 同一部法的上千件案子,這兩種段落幾乎逐字相同,沒有區別力,只會把檢索名額佔滿
KB_SKIP_ROLES = frozenset({"法規引述", "結語"})
# 已在跑或已完成的不必重送;與 08_reference_ingest.py 同一組狀態
DONE_STATUSES = frozenset({"INDEXED", "IN_PROGRESS"})
KB_DELETE_SETTLE_SECONDS = 30
EXECUTE_CONFIRMATION = "IMPORT-PAST-DECISIONS"
# KB 只收這五個鍵;其餘欄位一律回 DynamoDB 精查
KB_METADATA_KEYS = ("case_id", "case_type", "result", "appeal_article", "year")
# DynamoDB item 的完整欄位;空值存空字串不省略,省略欄位讀回來是 KeyError
DDB_FIELDS = (
    "case_id",
    "case_no",
    "year",
    "case_type",
    "case_subtype",
    "appeal_article",
    "issue",
    "result",
    "source_url",
    "source_file",
)

_BUCKET_RE = re.compile(r"^決定書-(.+)\.jsonl$")
_CLAUSE_RE = re.compile(r"^§77\((\d+)\)$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        metavar="CONFIRMATION",
        help=f"真正寫入 AWS 必須傳入 {EXECUTE_CONFIRMATION}",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number} JSON 解析失敗: {exc}") from exc
    return rows


def clause_to_appeal_article(clause: str) -> str:
    """"§77(2)" -> "77(2)";非 77 條款次(§79、複合、未判定)一律回空字串。"""
    m = _CLAUSE_RE.match(clause or "")
    return f"77({m.group(1)})" if m else ""


def crawl_rows() -> list[dict]:
    """爬蟲決定書 chunk -> 本腳本的共同形狀;留出年度不進來。"""
    if not CRAWL_CASE_CHUNK_DIR.is_dir():
        raise FileNotFoundError(
            f"找不到爬蟲決定書 chunk 目錄: {CRAWL_CASE_CHUNK_DIR};"
            "語料放在他處時以環境變數 CRAWL_CASE_CHUNK_DIR 指過去"
        )
    rows = []
    for path in sorted(CRAWL_CASE_CHUNK_DIR.glob("決定書-*.jsonl")):
        bucket_match = _BUCKET_RE.match(path.name)
        if not bucket_match:
            raise ValueError(f"檔名不符「決定書-{{案型}}.jsonl」: {path.name}")
        bucket = bucket_match.group(1)
        for row in load_jsonl(path):
            meta = row.get("metadata", {})
            year = str(meta.get("year", ""))
            if year in HOLDOUT_YEARS:
                continue
            case_id = str(meta.get("source_file", "")).strip()
            if not case_id:
                raise ValueError(f"案例缺少 source_file: {row.get('片段名')}")
            section = str(meta.get("section", ""))
            subtype = str(meta.get("case_type", ""))
            clause = str(meta.get("clause", ""))
            result = str(meta.get("result", ""))
            # header 沿用來源案由與款次原文,與 local 模式的 case_chunks 逐字相同
            header = f"【{year}年-{subtype}-{clause}-{result}】{section}欄"
            rows.append(
                {
                    "id": str(row["片段名"]),
                    "text": f"{header}\n{row.get('內容', '')}",
                    "paragraph_role": str(meta.get("paragraph_role", "")),
                    "case_id": case_id,
                    "case_no": str(meta.get("case_no", "")),
                    "year": year,
                    "case_type": bucket,
                    "case_subtype": subtype,
                    "appeal_article": clause_to_appeal_article(clause),
                    "issue": "",  # 爬蟲語料無爭點欄
                    "result": result,
                    "source_url": str(meta.get("source_url", "")),
                    "source_file": case_id,
                }
            )
    return rows


def official_rows() -> list[dict]:
    """官方語料 case_chunks.jsonl -> 同一個共同形狀;沒有 paragraph_role,整批都進 KB。"""
    if not CASE_CHUNKS_PATH.exists():
        raise FileNotFoundError(
            f"找不到官方決定書 chunk: {CASE_CHUNKS_PATH};請先跑 preprocessing/parse_decisions.py"
        )
    rows = []
    for row in load_jsonl(CASE_CHUNKS_PATH):
        meta = row.get("metadata", {})
        year = str(meta.get("year", ""))
        if year in HOLDOUT_YEARS:
            continue
        case_no = str(meta.get("case_no", ""))
        if not case_no:
            raise ValueError(f"案例缺少 case_no: {row.get('id')}")
        case_type = str(meta.get("case_type", ""))
        rows.append(
            {
                "id": str(row["id"]),
                "text": str(row.get("text", "")),
                "paragraph_role": "",
                "case_id": f"NTPC-{case_no}",
                "case_no": case_no,
                "year": year,
                "case_type": case_type,
                "case_subtype": case_type,
                "appeal_article": str(meta.get("appeal_article", "")),
                "issue": str(meta.get("issue", "")),
                "result": str(meta.get("result", "")),
                "source_url": "",  # 主辦方直接給的檔案,本來就沒有來源網址
                "source_file": str(meta.get("source_file", "")),
            }
        )
    return rows


def kb_rows(rows: list[dict]) -> list[dict]:
    """被排除的段落原文留在官方網站,靠 source_url 看。"""
    return [r for r in rows if r["paragraph_role"] not in KB_SKIP_ROLES]


def ddb_items(rows: list[dict]) -> list[dict]:
    """一案一筆總覽 item。同一案的多個 chunk 欄位相同,取排序最前的那筆即可。"""
    by_case: dict[str, dict] = {}
    for row in sorted(rows, key=lambda r: r["id"]):
        if row["case_id"] in by_case:
            continue
        by_case[row["case_id"]] = {field: row[field] for field in DDB_FIELDS}
    return list(by_case.values())


def build_document(row: dict) -> dict:
    """chunk -> IngestKnowledgeBaseDocuments 的一份文件。
    空字串的 metadata 一律省略:S3 Vectors 要求 stringValue 長度 >= 1,傳空字串會噴 ValidationException。"""
    attributes = [
        {"key": key, "value": {"type": "STRING", "stringValue": row[key]}}
        for key in KB_METADATA_KEYS
        if row[key] != ""
    ]
    return {
        "content": {
            "dataSourceType": "CUSTOM",
            "custom": {
                "customDocumentIdentifier": {"id": row["id"]},
                "sourceType": "IN_LINE",
                "inlineContent": {"type": "TEXT", "textContent": {"data": row["text"]}},
            },
        },
        "metadata": {"type": "IN_LINE_ATTRIBUTE", "inlineAttributes": attributes},
    }


def assert_rows(rows: list[dict], to_kb: list[dict], items: list[dict]) -> None:
    if any(r["year"] in HOLDOUT_YEARS for r in rows):
        raise ValueError("語料含 114 年留出案例")
    if any(r["paragraph_role"] in KB_SKIP_ROLES for r in to_kb):
        raise ValueError("KB 文件含應排除的段落角色")
    if len({r["id"] for r in to_kb}) != len(to_kb):
        raise ValueError("KB document id 重複")
    if len({i["case_id"] for i in items}) != len(items):
        raise ValueError("DynamoDB case_id 重複")
    if {r["case_id"] for r in rows} != {i["case_id"] for i in items}:
        raise ValueError("DynamoDB 的案件集合與語料不一致")
    missing = [i for i in items if not i["source_url"] and not i["source_file"]]
    if missing:
        raise ValueError(f"{len(missing)} 案既無 source_url 也無 source_file,前端會連一個原文入口都沒有")


def print_summary(rows: list[dict], to_kb: list[dict], items: list[dict]) -> None:
    if not items:
        raise ValueError("語料一筆案件都沒有,請確認 CRAWL_CASE_CHUNK_DIR 與 CASE_CHUNKS_PATH")
    skipped = Counter(r["paragraph_role"] for r in rows if r["paragraph_role"] in KB_SKIP_ROLES)
    by_result = Counter(i["result"] for i in items)
    by_type = Counter(i["case_type"] for i in items)
    with_url = sum(1 for i in items if i["source_url"])
    print(f"語料 chunk={len(rows)}  案件={len(items)}")
    print(f"KB 提交 chunk={len(to_kb)}  略過={len(rows) - len(to_kb)} {dict(skipped)}")
    print(f"DynamoDB item={len(items)}  有 source_url={with_url}  有 source_file={sum(1 for i in items if i['source_file'])}")
    print(f"決定結果分布: {dict(by_result)}")
    print(f"案型前 5: {by_type.most_common(5)}")
    print(f"item 範例: {json.dumps(items[0], ensure_ascii=False)}")


def assert_aws_scope(session: boto3.Session) -> None:
    identity = session.client("sts").get_caller_identity()
    if identity.get("Account") != ACCOUNT_ID:
        raise RuntimeError(f"AWS account 不符: expected={ACCOUNT_ID}, actual={identity.get('Account')}")
    if session.region_name != REGION:
        raise RuntimeError(f"AWS region 不符: expected={REGION}, actual={session.region_name}")


def sync_kb_documents(bedrock_agent, limiter, kb_id: str, ds_id: str, to_kb: list[dict]) -> list[dict]:
    """刪掉不在新語料裡的舊文件(留出年度、已排除的段落角色),回傳這次仍需提交的 chunk。
    已 INDEXED / IN_PROGRESS 的不重送,灌到一半憑證過期時重跑才不必整批重來。"""
    documents = base.list_kb_documents(bedrock_agent, limiter, kb_id, ds_id)
    wanted = {r["id"] for r in to_kb}
    status_by_id = {d["identifier"].get("custom", {}).get("id"): d.get("status", "") for d in documents}
    stale = [d["identifier"] for d in documents if d["identifier"].get("custom", {}).get("id") not in wanted]
    # FAILED 的 id 直接重送不會覆寫、只會再失敗一次;刪掉再送才會重新索引(實測)
    failed = [
        d["identifier"]
        for d in documents
        if d.get("status") == "FAILED" and d["identifier"].get("custom", {}).get("id") in wanted
    ]
    to_delete = stale + failed
    print(f"[SYNC] KB-CASE 既有文件={len(documents)};不在新語料裡的={len(stale)};FAILED 待重灌={len(failed)}")
    for start in range(0, len(to_delete), base.KB_DELETE_BATCH_SIZE):
        batch = to_delete[start : start + base.KB_DELETE_BATCH_SIZE]
        limiter.call(
            bedrock_agent.delete_knowledge_base_documents,
            knowledgeBaseId=kb_id,
            dataSourceId=ds_id,
            documentIdentifiers=batch,
        )
        print(f"  [DELETE] {min(start + len(batch), len(to_delete))}/{len(to_delete)}")
    if to_delete:
        # 刪除是非同步的,沒放完就重送會撞回同一個 FAILED 記錄
        print(f"  [WAIT] 等待刪除生效 {KB_DELETE_SETTLE_SECONDS} 秒")
        time.sleep(KB_DELETE_SETTLE_SECONDS)
    todo = [r for r in to_kb if status_by_id.get(r["id"]) not in DONE_STATUSES]
    print(f"[SYNC] 需提交={len(todo)};已在庫跳過={len(to_kb) - len(todo)}")
    return todo


def write_dynamodb(resource, items: list[dict]) -> None:
    """只設本腳本負責的欄位。整筆 put_item 會連同其他腳本回填的 law_ids/related_laws 一起清掉,
    而那種清除不會噴任何錯誤,要等到檢索少了東西才發現。"""
    table = resource.Table(DDB_PAST_DECISIONS_TABLE)
    fields = [f for f in DDB_FIELDS if f != "case_id"]
    update_expression = "SET " + ", ".join(f"#{f} = :{f}" for f in fields)
    names = {f"#{f}": f for f in fields}
    for i, item in enumerate(items, start=1):
        table.update_item(
            Key={"case_id": item["case_id"]},
            UpdateExpression=update_expression,
            ExpressionAttributeNames=names,
            ExpressionAttributeValues={f":{f}": item[f] for f in fields},
        )
        if i % 500 == 0:
            print(f"  [PROGRESS] DynamoDB {i}/{len(items)}")
    print(f"[UPDATE] DynamoDB 寫入 {len(items)} 案")


def verify_dynamodb(resource, items: list[dict]) -> None:
    """抽驗頭尾各一筆:欄位齊備、且至少有一個原文入口。"""
    table = resource.Table(DDB_PAST_DECISIONS_TABLE)
    for item in (items[0], items[-1]):
        got = table.get_item(Key={"case_id": item["case_id"]}, ConsistentRead=True).get("Item")
        if not got:
            raise RuntimeError(f"DynamoDB 驗證失敗,查無: {item['case_id']}")
        missing = [f for f in DDB_FIELDS if f not in got]
        if missing:
            raise RuntimeError(f"DynamoDB {item['case_id']} 缺欄位: {missing}")
        if not got["source_url"] and not got["source_file"]:
            raise RuntimeError(f"DynamoDB {item['case_id']} 沒有任何原文入口")
    print(f"[VERIFY] DynamoDB 抽驗通過: {items[0]['case_id']} / {items[-1]['case_id']}")


def main() -> None:
    args = parse_args()
    rows = crawl_rows() + official_rows()
    to_kb = kb_rows(rows)
    items = ddb_items(rows)
    assert_rows(rows, to_kb, items)
    print_summary(rows, to_kb, items)

    if args.execute != EXECUTE_CONFIRMATION:
        print(f"\n[DRY-RUN] 未寫入 AWS。要真的灌請加 --execute {EXECUTE_CONFIRMATION}")
        return

    resources = load_resources()
    kb_id = resources.get("kb_case_id")
    ds_id = resources.get("data_source_case_id")
    if not kb_id or not ds_id:
        print("[ERROR] resources.json 缺少 kb_case_id/data_source_case_id,請先執行 03_vectors_kb.py", file=sys.stderr)
        sys.exit(1)

    session = boto3.Session(region_name=REGION)
    assert_aws_scope(session)
    bedrock_agent = session.client("bedrock-agent")
    limiter = base.RateLimiter(base.BEDROCK_MIN_INTERVAL_SECONDS)

    todo = sync_kb_documents(bedrock_agent, limiter, kb_id, ds_id, to_kb)

    results = {"KB-CASE": {"success": 0, "fail": 0, "failures": []}}
    refing.ingest_all(
        bedrock_agent, kb_id, ds_id, [build_document(r) for r in todo], "KB-CASE", results, limiter
    )
    failures = results["KB-CASE"]["failures"]
    print(f"[KB] 成功={results['KB-CASE']['success']} 失敗={results['KB-CASE']['fail']}")
    if failures:
        INGEST_FAILURES_PATH.write_text(
            json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"[WARN] 失敗清單寫入 {INGEST_FAILURES_PATH}")

    ddb_resource = session.resource("dynamodb")
    write_dynamodb(ddb_resource, items)
    verify_dynamodb(ddb_resource, items)
    print("[DONE] 過往案例資料層灌入完成")


if __name__ == "__main__":
    main()
