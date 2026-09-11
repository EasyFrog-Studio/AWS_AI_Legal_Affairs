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


def test_documents_round_trips_with_all_three_check_states():
    """三槽的 DocumentCheck 三態(True/False/None)都要能存回讀回,不能在序列化這一段消失。"""
    from app.models import CaseDocument, DocumentCheck

    table = MagicMock()
    store = DynamoDBStore(table=table)
    case = _case().model_copy(
        update={
            "documents": {
                "appeal": CaseDocument(
                    slot="appeal", source="text", text="訴願書全文",
                    check=DocumentCheck(matched=True, method="rule", note="符合訴願書的文字特徵"),
                ),
                "service": CaseDocument(
                    slot="service", source="text", text="送達證書全文",
                    check=DocumentCheck(matched=False, method="rule", note="文字特徵更接近原處分書"),
                ),
                "disposition": CaseDocument(
                    slot="disposition", source="pdf", text="原處分書全文",
                    check=DocumentCheck(matched=None, method="none", note="規則判斷特徵不足"),
                ),
            }
        }
    )
    item = store._to_item(case)
    assert isinstance(item["documents"], str)  # 巢狀欄位序列化為 JSON 字串,同 f1/f2 等欄位
    table.get_item.return_value = {"Item": item}

    fetched = store.get(case.case_id)

    assert fetched.documents["appeal"].check.matched is True
    assert fetched.documents["service"].check.matched is False
    assert fetched.documents["disposition"].check.matched is None
    assert fetched.documents["disposition"].source == "pdf"


def test_documents_missing_from_old_item_falls_back_to_empty_dict_not_none():
    """documents 欄位加進去之前建立的舊資料,item 裡沒有這個 key;還原時要落 {} 而非 None——
    Case.documents 的型別是 dict[...] = {},不是 Optional,None 會讓 Pydantic 驗證直接失敗。"""
    table = MagicMock()
    store = DynamoDBStore(table=table)
    item = store._to_item(_case())
    del item["documents"]  # 模擬加這欄之前寫入的舊資料
    table.get_item.return_value = {"Item": item}

    fetched = store.get(_case().case_id)

    assert fetched.documents == {}


def _case_with_documents(appeal_text="訴願書全文", service_text="送達證書全文", disposition_text="原處分書全文"):
    from app.models import CaseDocument, DocumentCheck, build_input_text

    documents = {
        "appeal": CaseDocument(slot="appeal", source="text", text=appeal_text, check=DocumentCheck(matched=True)),
        "service": CaseDocument(slot="service", source="text", text=service_text, check=DocumentCheck(matched=True)),
        "disposition": CaseDocument(
            slot="disposition", source="text", text=disposition_text, check=DocumentCheck(matched=True)
        ),
    }
    return _case().model_copy(update={"documents": documents, "input_text": build_input_text(documents)})


def test_input_text_is_not_stored_twice_when_documents_exist():
    """DynamoDB 單筆上限 400KB,中文每字 3 bytes;documents 與 input_text 各存一份等於把
    卷證文字量直接加倍。input_text 是衍生值,讀回時重建即可。"""
    table = MagicMock()
    store = DynamoDBStore(table=table)

    item = store._to_item(_case_with_documents())

    assert item["input_text"] == ""  # 不另存一份
    assert "訴願書全文" in item["documents"]  # 單一真相在 documents


def test_input_text_is_rebuilt_from_documents_on_read():
    table = MagicMock()
    store = DynamoDBStore(table=table)
    case = _case_with_documents()
    table.get_item.return_value = {"Item": store._to_item(case)}

    fetched = store.get(case.case_id)

    assert fetched.input_text == case.input_text
    assert "【送達證書】" in fetched.input_text  # 分段標頭與 create_case 產出的一致


def test_old_item_without_documents_keeps_its_stored_input_text():
    """加 documents 欄位之前寫入的舊資料只有 input_text,讀回來不能變空字串。"""
    table = MagicMock()
    store = DynamoDBStore(table=table)
    item = store._to_item(_case())
    del item["documents"]
    table.get_item.return_value = {"Item": item}

    fetched = store.get(_case().case_id)

    assert fetched.input_text == "訴願書內容"


def test_oversized_case_raises_before_boto3_validation_exception():
    """寧可在寫入前擋下並說清楚,也不要讓案件在背景任務裡以 ValidationException 崩掉——
    那會落成 status=error,承辦人只看到一串英文。"""
    import pytest

    from app.store import CaseTooLargeError

    table = MagicMock()
    store = DynamoDBStore(table=table)
    huge = "訴" * 200_000  # UTF-8 每字 3 bytes -> 約 600KB,已超 400KB 單筆上限

    with pytest.raises(CaseTooLargeError):
        store.create(_case_with_documents(appeal_text=huge))

    table.put_item.assert_not_called()


def full_case():
    """滿載案件:三份文書 + F1~F4 + 20 版草稿。量大小用,也給人工量測腳本共用。"""
    from app.models import CaseInfo, DraftResult, DraftVersion, LawRef, ScreeningResult, SimilarCase

    draft = DraftResult(
        draft_type="不受理",
        fact="",
        reason="理由" * 500,
        main_text="訴願不受理。",
        cited_laws=["訴願法#77", "訴願法#14"],
    )
    return _case_with_documents(
        appeal_text="訴願書" * 700, service_text="送達證書" * 200, disposition_text="原處分書" * 700
    ).model_copy(
        update={
            "f1": CaseInfo(
                appellant="王大明",
                agency="新北市政府環境保護局",
                disposition_date="114年5月16日",
                disposition_no="新北環稽字第1號",
                disposition_summary="裁處罰鍰",
                case_type="廢棄物清理",
            ),
            "screening": ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期" * 100),
            "f2": [
                LawRef(
                    law_name="廢棄物清理法", article_no="27", text="條文" * 200,
                    amend_date="民國106年01月18日", source_key="markdown/相關法規/廢棄物清理法.md", relevance="相關",
                )
            ]
            * 5,
            "f3": [
                SimilarCase(
                    case_no="新北府訴字第1號", year="113", case_type="廢棄物清理", appeal_article="訴願法#77",
                    issue="逾期", result="不受理", summary="摘要" * 200, similarity_note="同款",
                    source_key="markdown/訴願決定書/1.md",
                )
            ]
            * 5,
            "f4": draft,
            "draft_versions": [
                DraftVersion(saved_at="2026-08-17T00:00:00", text="主　文 訴願不受理。 理　由 " + "理由" * 500)
            ]
            * 20,
        }
    )


def test_a_realistic_full_case_stays_well_under_the_size_threshold():
    """量一次真實 item 大小:三份文書 + F1~F4 + 20 版草稿。這一條把「會不會逼近 400KB」
    從推測變成已知數。低於門檻一半才算安全——
    超過就要調降草稿版本上限,或把 S3 offload 從「不做」提前排進來。"""
    from app.store import MAX_ITEM_BYTES, item_size_bytes

    store = DynamoDBStore(table=MagicMock())

    size = item_size_bytes(store._to_item(full_case()))

    assert size < MAX_ITEM_BYTES // 2, f"{size} bytes 已超門檻一半,須調降草稿版本上限或提前排 S3 offload"


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
