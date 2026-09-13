"""測 11_backfill_case_law_ids.py 的純函式邏輯與 apply() 的 dry-run 邊界。
不連 AWS:law_index 與 session 一律用假物件,ClientError 走 botocore 的真實例外類別。
"""
import importlib.util
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

_SCRIPT_DIR = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "backfill_case_law_ids", _SCRIPT_DIR / "11_backfill_case_law_ids.py"
)
backfill = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = backfill
_SPEC.loader.exec_module(backfill)


# ---- parse_related_laws:異質輸入 ----


@pytest.mark.parametrize(
    "raw, expected_parsed, expected_unparsed",
    [
        # 全形分隔,最常見的形狀
        ("訴願法 第1條；訴願法 第77條", [("訴願法", "1"), ("訴願法", "77")], []),
        # 半形分隔(語料未見,防禦性支援)
        ("訴願法 第79條;停車場法 第32條", [("訴願法", "79"), ("停車場法", "32")], []),
        # 「第N條之M」-> "N-M"
        ("停車場法 第40條之1", [("停車場法", "40-1")], []),
        # 項/款字尾被忽略,只取條
        ("訴願法 第56條第1項第5款", [("訴願法", "56")], []),
        # 無法辨識的片段進 unparsed,不影響同一句裡其他能解析的片段
        ("訴願法 第79條；亂七八糟不是條號", [("訴願法", "79")], ["亂七八糟不是條號"]),
        # 空字串:兩邊都是空
        ("", [], []),
    ],
)
def test_parse_related_laws_variants(raw, expected_parsed, expected_unparsed):
    parsed, unparsed = backfill.parse_related_laws(raw)
    assert parsed == expected_parsed
    assert unparsed == expected_unparsed


# ---- build_updates:多 chunk 聯集 ----


def test_build_updates_unions_multi_chunk_law_ids_in_appearance_order():
    law_index = {"訴願法#1": 101, "訴願法#77": 102, "停車場法#32": 103}
    rows = [
        {"case_id": "NTPC-1", "related_laws": "訴願法 第1條；訴願法 第77條"},
        # 同一案第二個 chunk,新增一條法規、重複一條,順序應保留先出現的
        {"case_id": "NTPC-1", "related_laws": "訴願法 第77條；停車場法 第32條"},
        {"case_id": "NTPC-2", "related_laws": "訴願法 第1條"},
    ]

    updates = backfill.build_updates(rows, law_index)

    assert updates["NTPC-1"]["law_ids"] == [101, 102, 103]
    assert updates["NTPC-1"]["unresolved"] == []
    assert "訴願法 第1條；訴願法 第77條" in updates["NTPC-1"]["related_laws"]
    assert "停車場法 第32條" in updates["NTPC-1"]["related_laws"]
    assert updates["NTPC-2"]["law_ids"] == [101]


def test_build_updates_records_unresolved_law_article_when_missing_from_index():
    law_index = {"訴願法#1": 101}
    rows = [{"case_id": "NTPC-3", "related_laws": "訴願法 第1條；噪音管制法 第8條"}]

    updates = backfill.build_updates(rows, law_index)

    assert updates["NTPC-3"]["law_ids"] == [101]
    assert updates["NTPC-3"]["unresolved"] == ["噪音管制法#8"]


def test_build_updates_skips_rows_with_empty_related_laws():
    updates = backfill.build_updates([{"case_id": "NTPC-4", "related_laws": ""}], {})
    assert updates == {}


# ---- apply:dry_run 邊界 ----


class _FakeTable:
    def __init__(self, raise_missing_for: set[str] | None = None):
        self.calls: list[dict] = []
        self._raise_missing_for = raise_missing_for or set()

    def update_item(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["Key"]["case_id"] in self._raise_missing_for:
            raise ClientError(
                {"Error": {"Code": "ConditionalCheckFailedException", "Message": "no such case"}},
                "UpdateItem",
            )


class _FakeResource:
    def __init__(self, table):
        self._table = table

    def Table(self, name):  # noqa: N802 - 比照 boto3.resource("dynamodb").Table(name)
        return self._table


class _FakeSession:
    def __init__(self, table):
        self._table = table

    def resource(self, name):
        return _FakeResource(self._table)


def test_apply_dry_run_never_calls_update_item():
    updates = {"NTPC-1": {"law_ids": [101], "related_laws": "訴願法 第1條", "unresolved": []}}

    stats = backfill.apply(None, updates, dry_run=True)

    assert stats["updated"] == 0
    assert stats["attempted"] == 1


def test_apply_non_dry_run_calls_update_item_with_condition_expression():
    table = _FakeTable()
    session = _FakeSession(table)
    updates = {"NTPC-1": {"law_ids": [101, 102], "related_laws": "訴願法 第1條", "unresolved": []}}

    stats = backfill.apply(session, updates, dry_run=False)

    assert len(table.calls) == 1
    call = table.calls[0]
    assert call["Key"] == {"case_id": "NTPC-1"}
    assert call["ConditionExpression"] == "attribute_exists(case_id)"
    assert call["ExpressionAttributeValues"][":ids"] == [101, 102]
    assert stats["updated"] == 1
    assert stats["missing"] == 0


def test_apply_non_dry_run_counts_missing_case_without_put():
    table = _FakeTable(raise_missing_for={"NTPC-GHOST"})
    session = _FakeSession(table)
    updates = {
        "NTPC-GHOST": {"law_ids": [], "related_laws": "訴願法 第1條", "unresolved": []},
    }

    stats = backfill.apply(session, updates, dry_run=False)

    assert stats["missing"] == 1
    assert stats["updated"] == 0
    # ConditionExpression 擋下的是 update_item 本身,腳本沒有任何 put_item 呼叫可言
    assert all("put_item" not in str(call) for call in table.calls)
