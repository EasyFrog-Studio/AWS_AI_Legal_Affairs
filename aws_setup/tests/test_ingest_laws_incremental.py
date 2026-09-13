"""測 12_ingest_laws_incremental.py 的純函式邏輯與 dry-run 邊界。
不連 AWS:DynamoDB session 一律用假物件;jsonl 讀取用 tmp_path 造小檔案。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_DIR = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "ingest_laws_incremental", _SCRIPT_DIR / "12_ingest_laws_incremental.py"
)
incr = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = incr
_SPEC.loader.exec_module(incr)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _row(law_name: str, article: str, source_url: str, *, deleted: str = "false") -> dict:
    return {
        "片段名": f"LAW-{law_name}-{article}",
        "來源檔": law_name,
        "文件類型": "法規",
        "段落角色": "",
        "內容": f"{law_name} 第 {article} 條\n內容示意。",
        "metadata": {
            "law_name": law_name,
            "article": article,
            "revised_date": "20240112",
            "deleted": deleted,
            "doc_type": "法規",
            "source": "全國法規資料庫 Open API https://law.moj.gov.tw/api/Ch/Law/JSON",
            "source_url": source_url,
        },
    }


# ---- load_new_candidates:pcode 有/無兩種 URL 組法,不呼叫 Open API/CSV ----


def test_load_new_candidates_builds_pcode_url_when_pcode_present(tmp_path):
    _write_jsonl(
        tmp_path / "法規-決定書引用補齊.jsonl",
        [_row("中央法規甲", "1", "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=O0020043")],
    )

    candidates = incr.load_new_candidates(tmp_path)

    assert len(candidates) == 1
    item = candidates[0]
    assert item["pcode"] == "O0020043"
    assert item["law_page_url"] == "https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=O0020043"
    assert (
        item["source_url"]
        == "https://law.moj.gov.tw/LawClass/LawSingle.aspx?pcode=O0020043&flno=1"
    )


def test_load_new_candidates_falls_back_to_raw_url_when_pcode_absent(tmp_path):
    raw_url = "https://www.laws.taipei.gov.tw/Law/LawSearch/LawArticleContent/FL004002"
    _write_jsonl(
        tmp_path / "法規-地方自治法規補齊.jsonl",
        [_row("臺北市公園管理自治條例", "1", raw_url)],
    )

    candidates = incr.load_new_candidates(tmp_path)

    assert len(candidates) == 1
    item = candidates[0]
    assert item["pcode"] == ""
    assert item["law_page_url"] == raw_url
    assert item["source_url"] == raw_url


def test_load_new_candidates_skips_deleted_and_rejects_bad_article(tmp_path):
    _write_jsonl(
        tmp_path / "法規-地方自治法規補齊.jsonl",
        [_row("已廢法規", "1", "", deleted="true")],
    )
    assert incr.load_new_candidates(tmp_path) == []

    _write_jsonl(
        tmp_path / "法規-決定書引用補齊.jsonl",
        [_row("壞條號法規", "第一條", "")],
    )
    with pytest.raises(ValueError):
        incr.load_new_candidates(tmp_path)


def test_load_new_candidates_missing_dir_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        incr.load_new_candidates(tmp_path / "does_not_exist")


# ---- assign_new_ids:append-only 編號、排除重疊、既有對照不變 ----


def test_assign_new_ids_starts_after_max_existing_and_excludes_overlap():
    existing_index = {"訴願法#1": 1, "訴願法#77": 13010}
    candidates = [
        {"law_article": "訴願法#1", "law_name": "訴願法", "article_no": "1"},  # 與既有重疊
        {"law_article": "新法規甲#2", "law_name": "新法規甲", "article_no": "2", "pcode": ""},
        {"law_article": "新法規甲#1", "law_name": "新法規甲", "article_no": "1", "pcode": ""},
    ]
    existing_snapshot = dict(existing_index)

    fresh, overlap = incr.assign_new_ids(candidates, existing_index, {})

    assert overlap == 1
    assert [item["law_article"] for item in fresh] == ["新法規甲#1", "新法規甲#2"]
    assert [item["law_id"] for item in fresh] == [13011, 13012]
    # 既有權威對照不得被本函式修改
    assert existing_index == existing_snapshot


def test_assign_new_ids_document_id_format_matches_kb_document():
    existing_index = {"訴願法#1": 1}
    candidates = [{"law_article": "新法規#1", "law_name": "新法規", "article_no": "1", "pcode": ""}]

    fresh, _ = incr.assign_new_ids(candidates, existing_index, {})

    assert fresh[0]["document_id"] == "LAW#00000002"


def test_assign_new_ids_cache_reuse_keeps_ids_stable_across_reruns():
    existing_index = {"訴願法#1": 13010}
    candidates = [
        {"law_article": "新法規甲#1", "law_name": "新法規甲", "article_no": "1", "pcode": ""},
        {"law_article": "新法規甲#2", "law_name": "新法規甲", "article_no": "2", "pcode": ""},
    ]

    cache: dict[str, int] = {}
    first_run, _ = incr.assign_new_ids(candidates, existing_index, cache)
    first_ids = {item["law_article"]: item["law_id"] for item in first_run}

    # 模擬重跑:同一份 cache(已被第一次呼叫填好)再跑一次,id 必須完全沿用
    second_run, overlap_second = incr.assign_new_ids(candidates, existing_index, cache)
    second_ids = {item["law_article"]: item["law_id"] for item in second_run}

    assert first_ids == second_ids == {"新法規甲#1": 13011, "新法規甲#2": 13012}
    assert overlap_second == 0


def test_assign_new_ids_cache_extends_for_newly_added_law_article():
    existing_index = {"訴願法#1": 13010}
    cache = {"新法規甲#1": 13011}
    candidates = [
        {"law_article": "新法規甲#1", "law_name": "新法規甲", "article_no": "1", "pcode": ""},
        {"law_article": "新法規乙#1", "law_name": "新法規乙", "article_no": "1", "pcode": ""},
    ]

    fresh, _ = incr.assign_new_ids(candidates, existing_index, cache)
    ids = {item["law_article"]: item["law_id"] for item in fresh}

    assert ids["新法規甲#1"] == 13011  # 沿用 cache,不重編
    assert ids["新法規乙#1"] == 13012  # 新項目接續編號
    assert cache["新法規乙#1"] == 13012  # cache 已更新供下次沿用


# ---- assert_existing_authoritative:append-only 前提檢核 ----


def test_assert_existing_authoritative_rejects_unexpected_count():
    with pytest.raises(RuntimeError):
        incr.assert_existing_authoritative({"訴願法#1": 1})


def test_assert_existing_authoritative_rejects_empty():
    with pytest.raises(RuntimeError):
        incr.assert_existing_authoritative({})


def test_assert_existing_authoritative_accepts_expected_shape():
    index = {f"law#{i}": i for i in range(1, incr.EXPECTED_EXISTING_COUNT + 1)}
    incr.assert_existing_authoritative(index)  # 不拋例外即通過


# ---- load_cache/save_cache 往返 ----


def test_cache_round_trip(tmp_path):
    cache_path = tmp_path / "law_incremental_ids.json"
    assert incr.load_cache(cache_path) == {}

    incr.save_cache(cache_path, {"新法規甲#1": 13011})
    assert incr.load_cache(cache_path) == {"新法規甲#1": 13011}


# ---- dry-run 不呼叫任何 AWS 寫入 ----


class _ScanOnlyTable:
    """只提供 scan;若程式碼誤呼叫 batch_writer/put_item 會直接 AttributeError,測試因此失敗。"""

    def __init__(self, items: list[dict]):
        self._items = items

    def scan(self, **kwargs):
        return {"Items": self._items}


class _ScanOnlyResource:
    def __init__(self, table):
        self._table = table

    def Table(self, name):  # noqa: N802 - 比照 boto3.resource("dynamodb").Table(name)
        return self._table


class _ScanOnlySession:
    """刻意不提供 client("bedrock-agent"),確認 dry-run 流程完全不需要它。"""

    def __init__(self, table):
        self._table = table

    def resource(self, name):
        return _ScanOnlyResource(self._table)


def test_scan_existing_law_index_paginates_and_never_writes():
    items = [{"law_id": 1, "law_article": "訴願法#1"}, {"law_id": 13010, "law_article": "訴願法#77"}]
    session = _ScanOnlySession(_ScanOnlyTable(items))

    index = incr.scan_existing_law_index(session)

    assert index == {"訴願法#1": 1, "訴願法#77": 13010}


def test_dry_run_plan_never_touches_write_or_bedrock_apis(tmp_path, monkeypatch):
    """完整跑一遍 dry-run 會用到的純流程(scan → assign → report),
    session 只有 scan 能力,程式若誤呼叫寫入/Bedrock 會因缺方法直接炸掉。"""
    existing_items = [{"law_id": i, "law_article": f"既有法規#{i}"} for i in range(1, incr.EXPECTED_EXISTING_COUNT + 1)]
    session = _ScanOnlySession(_ScanOnlyTable(existing_items))

    _write_jsonl(
        tmp_path / "法規-地方自治法規補齊.jsonl",
        [_row("新增地方法規", "1", "https://web.law.ntpc.gov.tw/xxx")],
    )
    cache_path = tmp_path / "law_incremental_ids.json"

    existing_index = incr.scan_existing_law_index(session)
    incr.assert_existing_authoritative(existing_index)
    candidates = incr.load_new_candidates(tmp_path)
    cache = incr.load_cache(cache_path)
    fresh, overlap = incr.assign_new_ids(candidates, existing_index, cache)
    incr.save_cache(cache_path, cache)
    incr.print_dry_run_report(fresh, overlap, existing_index)

    assert overlap == 0
    assert len(fresh) == 1
    assert fresh[0]["law_id"] == incr.EXPECTED_EXISTING_COUNT + 1
    assert incr.load_cache(cache_path) == {"新增地方法規#1": incr.EXPECTED_EXISTING_COUNT + 1}
