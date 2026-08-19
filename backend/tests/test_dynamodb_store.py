"""DynamoDBStore 單元測試:mock boto3 Table,驗證參數組裝與序列化,不做真實呼叫。"""
import json
from unittest.mock import MagicMock

from app.models import Case
from app.store import DynamoDBStore


def _case():
    return Case(
        case_id="c-abc12345",
        created_at="2026-08-17T00:00:00",
        title="測試案件",
        status="processing",
        current_stage="f1",
        source="text",
        input_text="訴願書內容",
    )


def test_create_calls_put_item_with_json_serialized_nested_fields():
    table = MagicMock()
    store = DynamoDBStore(table=table)
    case = _case()
    store.create(case)

    table.put_item.assert_called_once()
    item = table.put_item.call_args.kwargs["Item"]
    assert item["case_id"] == "c-abc12345"
    assert item["title"] == "測試案件"
    assert item["f1"] == ""  # None -> 空字串,不落地 null
    assert isinstance(item["f1"], str)


def test_create_serializes_nested_model_as_json_string():
    from app.models import CaseInfo

    table = MagicMock()
    store = DynamoDBStore(table=table)
    case = _case()
    case = case.model_copy(
        update={
            "f1": CaseInfo(
                appellant="王小明",
                agency="環保署",
                disposition_date="110年1月1日",
                disposition_no="環署字第1號",
                disposition_summary="罰鍰處分",
                case_type="廢棄物清理",
            )
        }
    )
    store.create(case)
    item = table.put_item.call_args.kwargs["Item"]
    assert isinstance(item["f1"], str)
    parsed = json.loads(item["f1"])
    assert parsed["appellant"] == "王小明"
    # 確認沒有 float 型別被送進 DynamoDB item(全部欄位皆為 str)
    assert all(isinstance(v, str) for v in item.values())


def test_get_item_uses_case_id_as_key():
    table = MagicMock()
    table.get_item.return_value = {"Item": None}
    store = DynamoDBStore(table=table)
    result = store.get("c-notfound")
    table.get_item.assert_called_once_with(Key={"case_id": "c-notfound"})
    assert result is None


def test_get_roundtrip_deserializes_json_fields():
    table = MagicMock()
    store = DynamoDBStore(table=table)
    case = _case()
    item = store._to_item(case)
    table.get_item.return_value = {"Item": item}
    fetched = store.get(case.case_id)
    assert fetched.case_id == case.case_id
    assert fetched.title == case.title
    assert fetched.f1 is None


def test_list_cases_paginates_with_last_evaluated_key():
    table = MagicMock()
    store = DynamoDBStore(table=table)
    item = store._to_item(_case())
    table.scan.side_effect = [
        {"Items": [item], "LastEvaluatedKey": {"case_id": "c-abc12345"}},
        {"Items": [item]},
    ]
    result = store.list_cases()
    assert len(result) == 2
    assert table.scan.call_count == 2
    _, kwargs = table.scan.call_args
    assert kwargs["ExclusiveStartKey"] == {"case_id": "c-abc12345"}


def test_update_reads_then_put_item():
    table = MagicMock()
    store = DynamoDBStore(table=table)
    case = _case()
    item = store._to_item(case)
    table.get_item.return_value = {"Item": item}

    updated = store.update(case.case_id, {"status": "done"})

    assert updated.status == "done"
    table.put_item.assert_called_once()
    put_item = table.put_item.call_args.kwargs["Item"]
    assert put_item["status"] == "done"
