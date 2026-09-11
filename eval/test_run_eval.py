"""執行器裡計分那一段的測試(不需 ollama / Postgres——只組 Case 物件,不跑 pipeline)。
執行:cd eval && python -m pytest test_run_eval.py
"""
from datetime import date

import pytest

from run_eval import LAYER_DEADLINE, LAYER_F1, _score_case, _score_fields
from scoring import CORRECT, UNSURE, WRONG

from app.models import Case, CaseInfo, DeadlineCheck, ScreeningResult


def _case(**fields) -> Case:
    return Case(case_id="t", created_at="2026-01-01T00:00:00", title="t", source="text", input_text="", **fields)


def _info(**fields) -> CaseInfo:
    base = dict(appellant="", agency="", disposition_date="", disposition_no="", disposition_summary="", case_type="")
    return CaseInfo(**{**base, **fields})


def test_a_field_name_that_is_not_on_CaseInfo_raises_instead_of_scoring_unsure():
    """答案鍵打錯欄位名時若回空字串,報告上會長得像「系統誠實回報抽不到」——評測自己的配置
    錯誤偽裝成受測系統的美德,是這套工具最不能犯的錯。"""
    with pytest.raises(KeyError):
        _score_fields(_info(agency="新北市政府環境保護局"), {"agancy": "新北市政府環境保護局"}, LAYER_F1)


def test_null_and_underscore_keys_are_not_scored():
    rows = _score_fields(
        _info(agency="新北市政府環境保護局"),
        {"agency": "新北市政府環境保護局", "disposition_no": None, "_note": "決定書未載"},
        LAYER_F1,
    )
    assert [(r["field"], r["result"]) for r in rows] == [("agency", CORRECT)]


def test_deadline_dates_are_compared_as_republic_era_values_not_as_iso_strings():
    case = _case(
        f1=_info(),
        deadline=DeadlineCheck(service_date=date(2023, 2, 7), due_date=date(2023, 3, 9)),
    )
    expected = {"deadline": {"service_date": "112年2月7日", "due_date": "112年3月10日", "filed_date": None}}

    rows = [r for r in _score_case(case, expected) if r["layer"] == LAYER_DEADLINE]

    assert [(r["field"], r["result"]) for r in rows] == [("service_date", CORRECT), ("due_date", WRONG)]


def test_a_date_the_system_could_not_determine_scores_unsure_not_wrong():
    case = _case(f1=_info(), deadline=DeadlineCheck(review_note="期間未計算,送達日無法認定"))

    rows = _score_case(case, {"deadline": {"service_date": "112年2月7日"}})

    assert [r["result"] for r in rows] == [UNSURE]


def test_a_case_that_never_reached_screening_scores_wrong_not_unsure():
    """screening 為 None 代表跑到一半死掉,不是系統說它不確定。"""
    case = _case(f1=_info())

    rows = _score_case(case, {"screening": {"passed": False, "clause": "77(2)"}})

    assert [r["result"] for r in rows] == [WRONG]


def test_screening_scores_against_the_clause_the_pipeline_settled_on():
    case = _case(
        f1=_info(),
        screening=ScreeningResult(passed=False, matched_clause="77條第2款", reasoning="逾期"),
    )

    rows = _score_case(case, {"screening": {"passed": False, "clause": "77(2)"}})

    assert [r["result"] for r in rows] == [CORRECT]
