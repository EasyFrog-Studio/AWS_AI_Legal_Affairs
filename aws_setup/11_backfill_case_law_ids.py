"""把爬蟲決定書語料 metadata.related_laws(自由文字)解析為 appeal_law_articles 的 law_id 清單,
回填到 appeal_past_decisions 每筆 item 的 law_ids(Number list)與 related_laws(原文字串)。

只 update_item 既有 case(ConditionExpression attribute_exists),不 put 新 item——
10_ingest_past_decisions.py 的 write_dynamodb() 才是那張表的權威寫入者,本腳本只補一個欄位。
必須在該腳本的重送完成後才能對表執行 --apply;可重跑。

預設 dry-run,只印解析與比對統計,不呼叫 update_item;真的要寫入才加 --apply。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

_SCRIPT_DIR = Path(__file__).resolve().parent


def _load_sibling(module_name: str, filename: str):
    """數字開頭的腳本檔名不是合法模組名,只能以路徑載入。"""
    spec = importlib.util.spec_from_file_location(module_name, _SCRIPT_DIR / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


ingest_past = _load_sibling("ingest_past_decisions", "10_ingest_past_decisions.py")

from config import (  # noqa: E402
    AWS_SETUP_DIR,
    CRAWL_CASE_CHUNK_DIR,
    DDB_LAW_TABLE,
    DDB_PAST_DECISIONS_TABLE,
    REGION,
)

CACHE_PATH = AWS_SETUP_DIR / ".cache" / "law_article_index.json"

# 語料抽樣 300 筆的實際分布:分隔符只有全形「；」,article 只出現「第N條」與「第N條之M」,
# 沒有項/款、沒有中文數字——(?:第\d+項)?(?:第\d+款)? 是防禦性保留,目前語料不會用到。
_SEP_RE = re.compile(r"[；;]")
_FRAGMENT_RE = re.compile(
    r"^(?P<name>.+?)\s*第(?P<num>\d+)條(?:之(?P<sub>\d+))?(?:第\d+項)?(?:第\d+款)?$"
)


def parse_related_laws(raw: str) -> tuple[list[tuple[str, str]], list[str]]:
    """把 "法規名 第N條；法規名 第M條之K" 拆成 [(法規名, article_no)],article_no 統一成 "N"/"N-M"。
    無法辨識的片段(原文)進第二個回傳值,不猜測。"""
    parsed: list[tuple[str, str]] = []
    unparsed: list[str] = []
    for fragment in _SEP_RE.split(raw or ""):
        fragment = fragment.strip()
        if not fragment:
            continue
        match = _FRAGMENT_RE.match(fragment)
        if not match:
            unparsed.append(fragment)
            continue
        article_no = match.group("num")
        if match.group("sub"):
            article_no = f"{article_no}-{match.group('sub')}"
        parsed.append((match.group("name").strip(), article_no))
    return parsed, unparsed


def read_related_laws_rows() -> list[dict[str, str]]:
    """比照 10_ 的 crawl_rows():同一組 glob/HOLDOUT_YEARS/source_file,
    但額外取 related_laws——10_ 的版本不需要這欄,不改那支腳本。"""
    if not CRAWL_CASE_CHUNK_DIR.is_dir():
        raise FileNotFoundError(
            f"找不到爬蟲決定書 chunk 目錄: {CRAWL_CASE_CHUNK_DIR};"
            "語料放在他處時以環境變數 CRAWL_CASE_CHUNK_DIR 指過去"
        )
    rows: list[dict[str, str]] = []
    for path in sorted(CRAWL_CASE_CHUNK_DIR.glob("決定書-*.jsonl")):
        for row in ingest_past.load_jsonl(path):
            meta = row.get("metadata", {})
            year = str(meta.get("year", ""))
            if year in ingest_past.HOLDOUT_YEARS:
                continue
            case_id = str(meta.get("source_file", "")).strip()
            if not case_id:
                continue
            rows.append(
                {
                    "case_id": case_id,
                    "related_laws": str(meta.get("related_laws", "")).strip(),
                }
            )
    return rows


def dedup_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """同一案的多個 chunk 常帶完全相同的 related_laws 原文;去重成 (case_id, raw) 對,
    後續統計與 build_updates 才不會把同一句話算好幾遍。"""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for row in rows:
        key = (row["case_id"], row["related_laws"])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def limit_rows(rows: list[dict[str, str]], limit: int | None) -> list[dict[str, str]]:
    if limit is None:
        return rows
    keep_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row["case_id"] in seen:
            continue
        if len(keep_ids) >= limit:
            break
        seen.add(row["case_id"])
        keep_ids.append(row["case_id"])
    keep = set(keep_ids)
    return [row for row in rows if row["case_id"] in keep]


def load_law_index(session: boto3.Session, *, use_cache: bool = True) -> dict[str, int]:
    """law_article("法規名#條號") -> law_id。一次性全表 scan,cache 到本機檔案。"""
    if use_cache and CACHE_PATH.exists():
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            return {k: int(v) for k, v in json.load(f).items()}

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

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_PATH.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False)
    return index


def build_updates(
    rows: list[dict[str, str]], law_index: dict[str, int]
) -> dict[str, dict[str, Any]]:
    """同一案多 chunk 的 related_laws 取聯集:law_ids 依語料出現順序去重,
    related_laws 存所有出現過的原文(通常同一案每個 chunk 都相同,聯集後仍是一句)。
    unresolved 混合兩種失敗:無法辨識的片段原文、辨識出但查無 law_id 的 "法規名#條號"。"""
    by_case: dict[str, dict[str, Any]] = {}
    for row in rows:
        raw = row["related_laws"]
        if not raw:
            continue
        entry = by_case.setdefault(
            row["case_id"], {"raw_seen": [], "law_ids": [], "unresolved": []}
        )
        if raw not in entry["raw_seen"]:
            entry["raw_seen"].append(raw)
        parsed, unparsed = parse_related_laws(raw)
        for fragment in unparsed:
            if fragment not in entry["unresolved"]:
                entry["unresolved"].append(fragment)
        for name, article_no in parsed:
            law_article = f"{name}#{article_no}"
            law_id = law_index.get(law_article)
            if law_id is None:
                if law_article not in entry["unresolved"]:
                    entry["unresolved"].append(law_article)
                continue
            if law_id not in entry["law_ids"]:
                entry["law_ids"].append(law_id)

    return {
        case_id: {
            "law_ids": entry["law_ids"],
            "related_laws": "；".join(entry["raw_seen"]),
            "unresolved": entry["unresolved"],
        }
        for case_id, entry in by_case.items()
    }


def apply(
    session: boto3.Session | None, updates: dict[str, dict[str, Any]], dry_run: bool
) -> dict[str, int]:
    """dry_run 時完全不碰 DynamoDB(連 table 物件都不取);
    非 dry_run 用 ConditionExpression attribute_exists(case_id) 擋住 put 新 item。"""
    if dry_run:
        print(f"[DRY-RUN] 待更新案件數={len(updates)};未寫入 DynamoDB")
        return {"updated": 0, "missing": 0, "attempted": len(updates)}

    table = session.resource("dynamodb").Table(DDB_PAST_DECISIONS_TABLE)
    stats = {"updated": 0, "missing": 0, "attempted": len(updates)}
    for case_id, entry in updates.items():
        try:
            table.update_item(
                Key={"case_id": case_id},
                UpdateExpression="SET law_ids = :ids, related_laws = :raw",
                ExpressionAttributeValues={
                    ":ids": [Decimal(law_id) for law_id in entry["law_ids"]],
                    ":raw": entry["related_laws"],
                },
                ConditionExpression="attribute_exists(case_id)",
            )
            stats["updated"] += 1
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                stats["missing"] += 1
                continue
            raise
    return stats


def print_report(
    rows: list[dict[str, str]], law_index: dict[str, int], updates: dict[str, dict[str, Any]]
) -> None:
    all_case_ids = {row["case_id"] for row in rows}
    cases_with_related = {row["case_id"] for row in rows if row["related_laws"]}
    total_fragments = 0
    parsed_ok = 0
    known_names = {article.split("#", 1)[0] for article in law_index}
    missing_name_counter: Counter[str] = Counter()
    missing_article_counter: Counter[str] = Counter()
    unparsed_counter: Counter[str] = Counter()

    for row in rows:
        raw = row["related_laws"]
        if not raw:
            continue
        parsed, unparsed = parse_related_laws(raw)
        total_fragments += len(parsed) + len(unparsed)
        for fragment in unparsed:
            unparsed_counter[fragment] += 1
        for name, article_no in parsed:
            law_article = f"{name}#{article_no}"
            if law_article in law_index:
                parsed_ok += 1
                continue
            if name in known_names:
                missing_article_counter[law_article] += 1
            else:
                missing_name_counter[name] += 1

    fully_resolved = sum(1 for entry in updates.values() if not entry["unresolved"])

    print(f"案件數(全部)={len(all_case_ids)}")
    print(f"有 related_laws 的案件數={len(cases_with_related)}")
    print(f"片段總數(去重後的 case,raw 組合)={total_fragments}")
    print(f"解析並在 appeal_law_articles 找到 law_id 的片段數={parsed_ok}")
    if total_fragments:
        print(f"片段解析成功率={parsed_ok / total_fragments:.2%}")
    if cases_with_related:
        print(
            f"完全解析成功的案件比例={fully_resolved}/{len(cases_with_related)}"
            f"={fully_resolved / len(cases_with_related):.2%}"
        )
    print(f"法規名在表中找不到 前20名: {missing_name_counter.most_common(20)}")
    print(f"條號找不到(法規名存在) 前20名: {missing_article_counter.most_common(20)}")
    print(f"完全無法辨識的片段 前20名: {unparsed_counter.most_common(20)}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dry-run",
        dest="dry_run",
        action="store_true",
        default=True,
        help="只印統計,不寫 DynamoDB(預設)",
    )
    parser.add_argument(
        "--apply",
        dest="dry_run",
        action="store_false",
        help="真正 update_item 寫入 appeal_past_decisions",
    )
    parser.add_argument("--limit", type=int, default=None, help="只處理前 N 個案件(除錯用)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = dedup_rows(limit_rows(read_related_laws_rows(), args.limit))

    session = boto3.Session(region_name=REGION)
    ingest_past.assert_aws_scope(session)
    law_index = load_law_index(session)
    print(f"law_index 筆數={len(law_index)}")

    updates = build_updates(rows, law_index)
    print_report(rows, law_index, updates)

    stats = apply(session, updates, dry_run=args.dry_run)
    print(f"[APPLY] {stats}")


if __name__ == "__main__":
    main()
