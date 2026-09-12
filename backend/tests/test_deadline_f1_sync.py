"""承辦人在 F1 改過送達日 / 收文日,期間認定要依改後的日期重算——期間卡片與 F1 顯示的是同一個事實。"""
from datetime import date

from fastapi.testclient import TestClient

from app import main as main_module
from app.config import settings
from app.models import Case, CaseDocument, CaseInfo
from app.pipeline import check_deadline_from_case
from tests.test_api import FIXTURES_DIR, _create_case_form, _headers

_APPEAL = (
    "系爭裁處書於114年5月28日送達訴願人戶籍地。訴願人住居所位於本市,無須扣除在途期間。"
    "訴願人遲至114年10月31日始提起訴願。"
)
_NOTICE = "如不服本處分,得自處分書送達之次日起三十日內,繕具訴願書經由本局向新北市政府提起訴願。"


def _case(service_text="送達時間:中華民國114年5月28日。", **fields) -> Case:
    return Case(
        case_id="c-f1sync",
        created_at="2026-09-12T00:00:00Z",
        title="F1 日期連動",
        source="text",
        input_text="",
        documents={
            "appeal": CaseDocument(slot="appeal", source="text", text=_APPEAL),
            "service": CaseDocument(slot="service", source="text", text=service_text),
            "disposition": CaseDocument(slot="disposition", source="text", text=f"主旨:裁處罰鍰。{_NOTICE}"),
        },
        **fields,
    )


def _info(**overrides) -> CaseInfo:
    fields = dict(
        appellant="王大明",
        agency="新北市政府環境保護局",
        disposition_date="民國114年5月16日",
        disposition_no="新北環稽字第1號",
        disposition_summary="裁處罰鍰",
        case_type="廢棄物清理",
    )
    fields.update(overrides)
    return CaseInfo(**fields)


def test_unedited_f1_keeps_the_dates_read_from_the_file():
    check = check_deadline_from_case(_case(f1=_info()), _info())

    assert check.service_date == date(2025, 5, 28)
    assert check.due_date == date(2025, 6, 27)
    assert check.overdue is True


def test_an_edited_service_date_drives_the_period():
    """送達日改成 114年6月10日:起算 6月11日,30 日末日 7月10日;收文日改成 7月5日 -> 未逾期。"""
    edited = _info(service_date="民國114年6月10日", appeal_filed_date="民國114年7月5日")

    check = check_deadline_from_case(_case(f1=_info()), edited)

    assert check.service_date == date(2025, 6, 10)
    assert check.due_date == date(2025, 7, 10)
    assert check.filed_date == date(2025, 7, 5)
    assert check.overdue is False
    assert "承辦人" in check.detail


def test_an_edited_filed_date_alone_keeps_the_certificate_service_date():
    edited = _info(appeal_filed_date="民國114年6月20日")

    check = check_deadline_from_case(_case(f1=_info()), edited)

    assert check.service_date == date(2025, 5, 28)
    assert check.due_date == date(2025, 6, 27)
    assert check.filed_date == date(2025, 6, 20)
    assert check.overdue is False


def test_the_original_extraction_is_the_baseline_after_a_second_edit():
    """第二次修改時 case.f1 已是人改過的值,基準要看 f1_system(模型原判)。"""
    original = _info()
    first_edit = _info(service_date="民國114年6月10日")
    second_edit = _info(service_date="民國114年6月10日", appellant="王大明(更正)")

    check = check_deadline_from_case(_case(f1=first_edit, f1_system=original), second_edit)

    assert check.service_date == date(2025, 6, 10)


def test_an_unreadable_edited_service_date_is_reported_not_silently_ignored():
    edited = _info(service_date="看不懂的日期")

    check = check_deadline_from_case(_case(f1=_info()), edited)

    assert check.service_date == date(2025, 5, 28)  # 仍依送達證書算
    assert "送達日期" in check.review_note and "無法辨識" in check.review_note


def test_an_edited_service_date_rescues_a_certificate_the_parser_could_not_read():
    """送達證書槽有字但抽不到送達時間 -> 原本退用自述日;承辦人補上送達日後照那個算。"""
    edited = _info(service_date="民國114年6月10日")

    check = check_deadline_from_case(_case("送達證書\n受送達人 王大明", f1=_info()), edited)

    assert check.service_date == date(2025, 6, 10)
    assert check.due_date == date(2025, 7, 10)
    assert "自述" not in check.review_note


def test_an_edited_service_date_is_not_reported_as_a_mismatch_with_the_certificate():
    """訴願書自述 5月28日、承辦人核定 6月10日:不符的是自述與人核定的日期,不得再說「與送達證書不符」。"""
    appeal = (
        "收受或知悉行政處分日期：114年5月28日 "
        "訴願人住居所位於本市,無須扣除在途期間。訴願人於114年10月31日提起訴願。"
    )
    case = _case(f1=_info())
    case.documents["appeal"] = CaseDocument(slot="appeal", source="text", text=appeal)

    unedited = check_deadline_from_case(case, _info())
    edited = check_deadline_from_case(case, _info(service_date="民國114年6月10日"))

    assert "不符" not in unedited.review_note  # 5月28日與證書一致
    assert edited.service_date == date(2025, 6, 10)
    assert "不符" not in edited.review_note


def test_patch_f1_recomputes_the_deadline_from_the_edited_dates(monkeypatch):
    monkeypatch.setattr(settings, "MOCK_DATA_DIR", str(FIXTURES_DIR))
    client = TestClient(main_module.app)
    case_id = client.post("/api/cases", data=_create_case_form(), headers=_headers()).json()["case_id"]
    case = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert case["deadline"]["service_date"] == "2021-03-10"

    resp = client.patch(
        f"/api/cases/{case_id}/f1",
        json={**case["f1"], "service_date": "民國110年4月1日", "appeal_filed_date": "民國110年5月1日"},
        headers=_headers(),
    )

    assert resp.status_code == 200
    stored = client.get(f"/api/cases/{case_id}", headers=_headers()).json()
    assert stored["deadline"]["service_date"] == "2021-04-01"
    assert stored["deadline"]["filed_date"] == "2021-05-01"
    assert stored["deadline"]["overdue"] is False
