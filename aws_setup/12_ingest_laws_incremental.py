"""把 D:\\Docker\\new_version 的兩份新法規 jsonl(967 部、28,164 條)以 append-only 方式
追加進 DynamoDB appeal_law_articles 與 Bedrock KB-LAW,既有 13,010 筆 law_id 一個都不變。

與 05_/06_ 的差異:
- 05_/06_ 的 law_id 由「候選全集排序後全量編號」決定,語料一變既有 law_id 就整批位移。
- 本腳本先掃描既有表取得 law_article↔law_id 權威對照(視為不可變),新法條只用
  max(既有 law_id)+1 起連續編號,寫回 DynamoDB 也只 put 新增筆,不動既有任何一筆。
- pcode 直接由新 jsonl 的 metadata.source_url 解析(含 pcode= 才取),不呼叫
  law.moj Open API、不讀任何 CSV——398 部地方自治法規本就查不到 pcode,原樣沿用
  jsonl 裡已經寫好的市府網址。

安全條件:
- STS 帳號必須是 config.ACCOUNT_ID,region 固定為 config.REGION。
- 只允許操作 config.DDB_LAW_TABLE 與 resources.json 的 KB-LAW / data source。
- 預設 dry-run:只掃描 DynamoDB(唯讀)並印統計,不呼叫任何寫入 API、不呼叫 Bedrock。
- 必須傳入 --execute INGEST-LAWS-INCREMENTAL 才會寫 DynamoDB 與送 KB。
- 沿用 05_/06_ 的 1.1 秒節流器,嚴格低於 1 RPS。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from config import ACCOUNT_ID, AWS_SETUP_DIR, DDB_LAW_TABLE, REGION, load_resources

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_sibling(module_name: str, filename: str):
    """數字開頭的腳本檔名不是合法模組名,只能以路徑載入(比照 11_backfill_case_law_ids.py)。"""
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# ingest_all 已在自己內部載入 05_reset_law_sample.py 並存成 .base,兩支都不改、只重用函式
ingest_all = _load_sibling("ingest_all_laws", "06_ingest_all_laws.py")
base = ingest_all.base

# 語料位置:D:\Docker\new_version 是本次任務給定的暫存路徑,環境變數可覆蓋(測試/未來搬移用)
NEW_LAW_DIR = Path(
    os.environ.get(
        "NEW_LAW_DIR",
        r"D:\Docker\new_version\爬蟲集-其他縣市\chunk\法條",
    )
)
LAW_ALL_BASE_URL = "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode="
LAW_SINGLE_BASE_URL = "https://law.moj.gov.tw/LawClass/LawSingle.aspx"
CACHE_PATH = AWS_SETUP_DIR / ".cache" / "law_incremental_ids.json"
EXPECTED_EXISTING_COUNT = 13010
EXECUTE_CONFIRMATION = "INGEST-LAWS-INCREMENTAL"
SAMPLE_PRINT_COUNT = 3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--execute",
        metavar="CONFIRMATION",
        help=f"真正寫入 AWS 時必須給定精確字串 {EXECUTE_CONFIRMATION}",
    )
    return parser.parse_args()


def load_new_candidates(law_dir: Path) -> list[dict[str, Any]]:
    """讀新增法條 jsonl,欄位邏輯比照 05_reset_law_sample.py:load_candidates(),
    差別只在 pcode 不查 CSV/Open API,直接解析 jsonl 自帶的 metadata.source_url。"""
    if not law_dir.exists():
        raise FileNotFoundError(f"找不到新增法條來源目錄:{law_dir}")

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(law_dir.glob("法規-*.jsonl")):
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
                if not base.ARTICLE_RE.fullmatch(article_no):
                    raise ValueError(f"{path.name}:{line_no} 非預期條號:{article_no!r}")
                law_article = f"{law_name}#{article_no}"
                if law_article in seen:
                    continue
                seen.add(law_article)

                raw_source_url = str(meta.get("source_url") or "").strip()
                pcode = base.pcode_from_url(raw_source_url)
                if pcode:
                    law_page_url = f"{LAW_ALL_BASE_URL}{pcode}"
                    source_url = f"{LAW_SINGLE_BASE_URL}?pcode={pcode}&flno={article_no}"
                else:
                    # 地方自治法規查無 pcode:原樣沿用 jsonl 裡已經寫好的市府查詢網址
                    law_page_url = raw_source_url
                    source_url = raw_source_url

                revised_date = str(meta.get("revised_date") or "").strip()
                candidates.append(
                    {
                        "law_article": law_article,
                        "law_name": law_name,
                        "article_no": article_no,
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
    return candidates


def scan_existing_law_index(session: boto3.Session) -> dict[str, int]:
    """appeal_law_articles 分頁 scan,只投影 law_id/law_article,回傳權威對照。"""
    table = session.resource("dynamodb").Table(DDB_LAW_TABLE)
    index: dict[str, int] = {}
    scan_kwargs: dict[str, Any] = {"ProjectionExpression": "law_id, law_article"}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get("Items", []):
            index[str(item["law_article"])] = int(item["law_id"])
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key
    return index


def assert_existing_authoritative(existing_index: dict[str, int]) -> None:
    """append-only 的前提:既有表必須恰是已知的 13,010 筆,否則拒絕在未知基礎上編號。"""
    if not existing_index:
        raise RuntimeError("appeal_law_articles 掃描結果為空,拒絕在空表上做 append-only 判斷")
    if len(existing_index) != EXPECTED_EXISTING_COUNT:
        raise RuntimeError(f"既有筆數不是預期的 {EXPECTED_EXISTING_COUNT}:{len(existing_index)}")
    max_id = max(existing_index.values())
    if max_id != EXPECTED_EXISTING_COUNT:
        raise RuntimeError(f"既有 law_id 最大值不是預期的 {EXPECTED_EXISTING_COUNT}:{max_id}")


def load_cache(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return {k: int(v) for k, v in json.load(f).items()}


def save_cache(path: Path, cache: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2, sort_keys=True)


def assign_new_ids(
    candidates: list[dict[str, Any]],
    existing_index: dict[str, int],
    cache: dict[str, int],
) -> tuple[list[dict[str, Any]], int]:
    """回傳 (新項目 list, 重疊筆數)。law_article 已在既有表者排除、不編號、不寫回 cache。
    新項目依 (law_name, 條號) 排序;cache 有的沿用其 law_id(保證重跑冪等),
    沒有的從 max(既有, cache)+1 起連續編號並寫回 cache 供下次沿用。既有對照不被修改。"""
    overlap = 0
    fresh: list[dict[str, Any]] = []
    for item in candidates:
        if item["law_article"] in existing_index:
            overlap += 1
            continue
        fresh.append(dict(item))

    fresh.sort(key=lambda item: (item["law_name"], ingest_all.article_sort_key(item["article_no"])))

    max_id = max(existing_index.values(), default=0)
    if cache:
        max_id = max(max_id, max(cache.values()))
    next_id = max_id + 1

    for item in fresh:
        cached_id = cache.get(item["law_article"])
        if cached_id is None:
            cached_id = next_id
            cache[item["law_article"]] = cached_id
            next_id += 1
        item["law_id"] = cached_id
        item["document_id"] = f"LAW#{item['law_id']:08d}"

    if len({item["law_id"] for item in fresh}) != len(fresh):
        raise RuntimeError("新項目 law_id 不唯一")
    if len({item["law_article"] for item in fresh}) != len(fresh):
        raise RuntimeError("新項目 law_article 不唯一")

    return fresh, overlap


def print_dry_run_report(
    fresh: list[dict[str, Any]], overlap: int, existing_index: dict[str, int]
) -> None:
    with_pcode = sum(1 for item in fresh if item["pcode"])
    without_pcode = len(fresh) - with_pcode
    law_id_range = [fresh[0]["law_id"], fresh[-1]["law_id"]] if fresh else [None, None]
    print(
        json.dumps(
            {
                "existing_count": len(existing_index),
                "new_count": len(fresh),
                "overlap_count": overlap,
                "law_id_range": law_id_range,
                "with_pcode": with_pcode,
                "without_pcode": without_pcode,
            },
            ensure_ascii=False,
            indent=2,
        )
    )

    if not fresh:
        return
    sample_indexes = sorted({0, len(fresh) // 2, len(fresh) - 1})
    print(f"[SAMPLE] 抽 {len(sample_indexes)} 筆組好的 DynamoDB item 與 KB document:")
    for idx in sample_indexes:
        item = fresh[idx]
        ddb_item = {key: item[key] for key in base.DYNAMODB_FIELDS if key != "law_id"}
        ddb_item["law_id"] = item["law_id"]
        print(json.dumps(ddb_item, ensure_ascii=False, indent=2))
        print(json.dumps(base.kb_document(item), ensure_ascii=False, indent=2))


def assert_table_scope(session: boto3.Session) -> None:
    """dry-run 與 execute 共用的最小安全檢查:只碰 STS + DynamoDB describe_table,不打 Bedrock。"""
    identity = session.client("sts").get_caller_identity()
    if identity["Account"] != ACCOUNT_ID:
        raise RuntimeError(f"AWS 帳號不符:預期 {ACCOUNT_ID},實際 {identity['Account']}")
    table = session.client("dynamodb").describe_table(TableName=DDB_LAW_TABLE)["Table"]
    if table["TableStatus"] != "ACTIVE":
        raise RuntimeError(f"{DDB_LAW_TABLE} 狀態不是 ACTIVE:{table['TableStatus']}")
    print(f"[VERIFY] account={identity['Account']} region={REGION} table={DDB_LAW_TABLE} status=ACTIVE")


def write_new_dynamodb(
    session: boto3.Session, fresh: list[dict[str, Any]], existing_index: dict[str, int]
) -> None:
    """batch_writer 沒有 ConditionExpression,寫入前先在本機確認新 law_id 與既有零交集。"""
    existing_ids = set(existing_index.values())
    conflict = [item["law_id"] for item in fresh if item["law_id"] in existing_ids]
    if conflict:
        raise RuntimeError(f"新 law_id 與既有表衝突,拒絕寫入:{conflict}")

    table = session.resource("dynamodb").Table(DDB_LAW_TABLE)
    with table.batch_writer(overwrite_by_pkeys=["law_id"]) as batch:
        for index, source in enumerate(fresh, 1):
            item = {key: source[key] for key in base.DYNAMODB_FIELDS}
            item["law_id"] = Decimal(source["law_id"])
            batch.put_item(Item=item)
            if index % 1000 == 0:
                print(f"  [DDB] 已寫入 {index}/{len(fresh)}")
    print(f"[DDB] 新增寫入完成:{len(fresh)}")


def print_final_stats(
    session: boto3.Session, bedrock: Any, limiter: Any, kb_id: str, data_source_id: str
) -> None:
    table_count = len(scan_existing_law_index(session))
    documents = ingest_all.list_kb_documents(bedrock, limiter, kb_id, data_source_id)
    status_counts = Counter(doc.get("status", "UNKNOWN") for doc in documents)
    print(
        json.dumps(
            {
                "table_count": table_count,
                "kb_total": len(documents),
                "kb_status_counts": dict(status_counts),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main() -> None:
    args = parse_args()

    session = boto3.Session(region_name=REGION)
    assert_table_scope(session)
    existing_index = scan_existing_law_index(session)
    assert_existing_authoritative(existing_index)

    candidates = load_new_candidates(NEW_LAW_DIR)
    cache = load_cache(CACHE_PATH)
    fresh, overlap = assign_new_ids(candidates, existing_index, cache)
    save_cache(CACHE_PATH, cache)

    print_dry_run_report(fresh, overlap, existing_index)

    if args.execute is None:
        print(f"[DRY-RUN] 未寫入 DynamoDB、未呼叫 Bedrock;要執行須傳 --execute {EXECUTE_CONFIRMATION}")
        return
    if args.execute != EXECUTE_CONFIRMATION:
        raise SystemExit(f"確認字串錯誤;必須是 {EXECUTE_CONFIRMATION}")

    resources = load_resources()
    kb_id, data_source_id = ingest_all.assert_live_scope(session, resources)
    write_new_dynamodb(session, fresh, existing_index)

    limiter = base.RateLimiter(base.BEDROCK_MIN_INTERVAL_SECONDS)
    bedrock = session.client("bedrock-agent")
    ingest_all.ingest_missing_kb(bedrock, limiter, kb_id, data_source_id, fresh)
    ingest_all.wait_and_verify_kb(bedrock, limiter, kb_id, data_source_id, fresh)
    print_final_stats(session, bedrock, limiter, kb_id, data_source_id)
    print(f"[DONE] {len(fresh)} 筆新法規已 append-only 匯入 DynamoDB 與 Bedrock KB-LAW")


if __name__ == "__main__":
    try:
        main()
    except (ClientError, OSError, RuntimeError, TimeoutError, ValueError) as error:
        print(f"[ERROR] {error}", file=sys.stderr)
        raise SystemExit(1)
