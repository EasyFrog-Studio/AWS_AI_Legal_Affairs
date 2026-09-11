"""scoring 純函式測試(不需 ollama / Postgres)。
執行:cd eval && python -m pytest test_scoring.py
"""
from scoring import CORRECT, UNSURE, WRONG, clause_key, normalize_roc_date, score_field, score_screening, tally


def test_document_numbers_match_across_dash_and_width_variants():
    """同一個文號在決定書與卷證裡會寫成不同的連字號與全半形,那不是抽錯。"""
    assert score_field("新北環稽字第41–112–010273號", "新北環稽字第41-112-010273號") == CORRECT
    assert score_field("新北環稽字第４１-１１２-０１０２７３號", "新北環稽字第41-112-010273號") == CORRECT


def test_a_different_document_number_is_wrong_not_forgiven():
    assert score_field("新北環稽字第41-112-010999號", "新北環稽字第41-112-010273號") == WRONG


def test_roc_dates_compare_by_value_not_by_spelling():
    assert score_field("民國 112 年 1 月 10 日", "112年1月10日", is_date=True) == CORRECT
    assert score_field("112年01月10日", "112年1月10日", is_date=True) == CORRECT
    assert score_field("112年1月11日", "112年1月10日", is_date=True) == WRONG


def test_unparseable_date_falls_back_to_string_compare_without_guessing():
    assert normalize_roc_date("無法辨識") == "無法辨識"
    assert score_field("無法辨識", "112年1月10日", is_date=True) == WRONG


def test_the_prompts_honest_miss_value_counts_as_unsure_not_wrong():
    """抽不到時 prompt 要求填「未載明」。把它算成誤判等於逼系統改成亂猜。"""
    assert score_field("未載明", "新北環稽字第41-112-010273號") == UNSURE
    assert score_field("", "新北環稽字第41-112-010273號") == UNSURE


def test_screening_needs_the_clause_to_match_not_just_the_verdict():
    expected = {"passed": False, "clause": "77(2)"}
    assert score_screening(False, "77條第2款", "", expected) == CORRECT
    assert score_screening(False, "77條第8款", "", expected) == WRONG
    assert score_screening(True, None, "", expected) == WRONG


def test_admissible_cases_have_no_clause_to_compare():
    expected = {"passed": True, "clause": None}
    assert score_screening(True, None, "", expected) == CORRECT
    assert score_screening(False, "77條第2款", "", expected) == WRONG


def test_a_conclusion_that_still_needs_human_confirmation_is_never_scored_correct():
    """系統標了 review_note 就是沒有主張那個結論,即使碰巧與答案相同也不能記成答對。"""
    expected = {"passed": False, "clause": "77(2)"}
    assert score_screening(False, "77條第2款", "期間未計算,送達日無法認定", expected) == UNSURE
    assert score_screening(True, None, "款次不在白名單", expected) == UNSURE


def test_clause_key_accepts_both_digit_systems_and_refuses_garbage():
    assert clause_key("77條第2款") == "77(2)"
    assert clause_key("77條第二款") == "77(2)"
    assert clause_key("訴願法第 77 條第 8 款") == "77(8)"
    assert clause_key("不合法定格式") is None
    assert clause_key(None) is None


def test_tally_reports_the_wrong_rate_separately_from_the_unsure_bucket():
    stats = tally([CORRECT, CORRECT, WRONG, UNSURE])
    assert stats == {"correct": 2, "wrong": 1, "unsure": 1, "total": 4, "wrong_rate": 0.25}
    assert tally([])["wrong_rate"] == 0.0


def test_case_type_matches_whenever_the_answer_names_the_right_law():
    """檔名標註是「違反廢棄物清理法事件」,系統輸出「廢棄物清理法」;指的是同一部法就算對。"""
    from scoring import score_case_type

    assert score_case_type("廢棄物清理法", "廢棄物清理法") == CORRECT
    assert score_case_type("違反廢棄物清理法事件", "廢棄物清理法") == CORRECT
    assert score_case_type("廢棄物清理", "廢棄物清理法") == WRONG  # 少一個字就是另一個詞,不放行
    assert score_case_type("空氣污染防制法", "廢棄物清理法") == WRONG
    assert score_case_type("未分類", "廢棄物清理法") == UNSURE


def test_a_trailing_號_does_not_make_the_same_document_number_wrong():
    """決定書內文寫「新北環稽字第41-112-010273」,卷證寫「…010273號」,指的是同一份文書。"""
    assert score_field("新北環稽字第41-112-010273號", "新北環稽字第41-112-010273") == CORRECT
    assert score_field("新北環稽字第41-112-010273", "新北環稽字第41-112-010273號") == CORRECT
    assert score_field("新北環稽字第41-112-010274號", "新北環稽字第41-112-010273") == WRONG


def test_case_type_refuses_a_runaway_answer_that_merely_contains_the_law_name():
    """B 部分要防的正是模型整段抄寫;計分若只看「有沒有出現法規名」就會替那種輸出背書。"""
    from scoring import score_case_type

    runaway = "廢棄物清理法第27條第8款,並依同法第50條第3款及裁罰基準,前開內容係指…"
    assert score_case_type(runaway, "廢棄物清理法") == WRONG
    assert score_case_type("違反廢棄物清理法事件", "廢棄物清理法") == CORRECT
    assert score_case_type("廢棄物清理法", "違反廢棄物清理法事件") == CORRECT
