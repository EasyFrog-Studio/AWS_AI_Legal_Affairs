"""§77(1) 必要記載事項自動判、§77(3) 當事人適格自動判。"""
from app.models import CaseInfo, ScreeningResult, StandingAssessment
from app.procedural_checks import (
    CorrectionNotice,
    StandingCheck,
    apply_article_77_1,
    apply_article_77_3,
    check_required_fields,
    check_standing,
    resolve_standing_assessment,
)


def _info(**overrides) -> CaseInfo:
    base = dict(
        appellant="王大明",
        agency="彰化縣環境保護局",
        disposition_date="110年3月5日",
        disposition_no="彰環廢字第1號",
        disposition_summary="裁處罰鍰",
        case_type="廢棄物清理",
        appeal_reasons=["原處分認定事實有誤"],
        receipt_date="110年3月10日",
    )
    base.update(overrides)
    return CaseInfo(**base)


def _screening(**overrides) -> ScreeningResult:
    base = dict(passed=True, matched_clause=None, reasoning="無不受理事由")
    base.update(overrides)
    return ScreeningResult(**base)


# ---------- check_required_fields:九款中可靠對應的四款各缺一款 ----------


def test_check_required_fields_all_present_reports_no_missing():
    check = check_required_fields(_info())
    assert check.missing == []


def test_check_required_fields_missing_appellant():
    check = check_required_fields(_info(appellant=""))
    assert any("第一款" in m for m in check.missing)


def test_check_required_fields_missing_agency():
    check = check_required_fields(_info(agency="未載明"))
    assert any("第三款" in m for m in check.missing)


def test_check_required_fields_missing_appeal_reasons():
    check = check_required_fields(_info(appeal_reasons=[]))
    assert any("第五款" in m for m in check.missing)


def test_check_required_fields_missing_receipt_date():
    check = check_required_fields(_info(receipt_date=""))
    assert any("第六款" in m for m in check.missing)


def test_check_required_fields_placeholder_未載明_counts_as_missing():
    """F1 對抽不到依據的欄位填「未載明」,這是缺漏,不是有內容。"""
    check = check_required_fields(_info(receipt_date="未載明"))
    assert any("第六款" in m for m in check.missing)


def test_check_required_fields_missing_both_appellant_and_agency_is_not_correctable():
    """訴願人與原處分機關皆缺,案件連基本身份都特定不了,無法補正。"""
    check = check_required_fields(_info(appellant="", agency=""))
    assert check.correctable is False


def test_check_required_fields_missing_reasons_only_is_correctable():
    check = check_required_fields(_info(appeal_reasons=[]))
    assert check.correctable is True


# ---------- apply_article_77_1:五種路徑 ----------


def test_apply_77_1_all_present_leaves_screening_untouched():
    screening = _screening()
    result = apply_article_77_1(screening, check_required_fields(_info()), None)
    assert result == screening


def test_apply_77_1_not_correctable_overrides_to_inadmissible():
    """姓名與機關皆缺,不能補正,自動覆寫為第1款不受理,並標記待人工確認。"""
    screening = _screening()
    check = check_required_fields(_info(appellant="", agency=""))

    result = apply_article_77_1(screening, check, None)

    assert result.passed is False
    assert result.matched_clause == "77條第1款"
    assert result.review_note != ""  # 自動判仍須留下複核記號


def test_apply_77_1_correctable_without_notice_does_not_override():
    """缺漏可補正,卷內無補正通知——不得逕採不受理結論,只標記應通知補正。"""
    screening = _screening()
    check = check_required_fields(_info(appeal_reasons=[]))

    result = apply_article_77_1(screening, check, None)

    assert result.passed is True  # 未被覆寫
    assert "應通知補正" in result.review_note or "補正" in result.review_note


def test_apply_77_1_correctable_with_notice_overdue_uncorrected_overrides():
    """已通知補正且逾期未補正,才成立第1款不受理。"""
    screening = _screening()
    check = check_required_fields(_info(appeal_reasons=[]))
    notice = CorrectionNotice(notified=True, corrected=False, overdue=True)

    result = apply_article_77_1(screening, check, notice)

    assert result.passed is False
    assert result.matched_clause == "77條第1款"
    assert result.review_note != ""


def test_apply_77_1_correctable_with_notice_corrected_does_not_override():
    """已通知且已補正,視為齊備,不覆寫也不留標記。"""
    screening = _screening()
    check = check_required_fields(_info(appeal_reasons=[]))
    notice = CorrectionNotice(notified=True, corrected=True, overdue=False)

    result = apply_article_77_1(screening, check, notice)

    assert result == screening


def test_apply_77_1_correctable_with_notice_not_yet_overdue_does_not_override():
    """已通知但補正期限尚未屆至,結果未定,不得預先判不受理。"""
    screening = _screening()
    check = check_required_fields(_info(appeal_reasons=[]))
    notice = CorrectionNotice(notified=True, corrected=False, overdue=False)

    result = apply_article_77_1(screening, check, notice)

    assert result.passed is True
    assert result.review_note != ""


# ---------- check_standing:比對相對人與訴願人 ----------


def test_check_standing_consistent_when_recipient_matches_appellant():
    check = check_standing(_info(appellant="王大明", disposition_recipient="王大明"))
    assert check.consistent is True


def test_check_standing_inconsistent_when_recipient_differs():
    check = check_standing(_info(appellant="王大明", disposition_recipient="李小華"))
    assert check.consistent is False


def test_check_standing_unknown_when_recipient_is_blank():
    """任一欄空白時不猜,回 None——不是「一致」也不是「不一致」。"""
    check = check_standing(_info(appellant="王大明", disposition_recipient=""))
    assert check.consistent is None


def test_check_standing_unknown_when_recipient_is_placeholder():
    check = check_standing(_info(appellant="王大明", disposition_recipient="未載明"))
    assert check.consistent is None


# ---------- apply_article_77_3:五種路徑 ----------


def test_apply_77_3_unknown_consistency_does_not_override():
    screening = _screening()
    result = apply_article_77_3(screening, StandingCheck(consistent=None))
    assert result == screening


def test_apply_77_3_consistent_does_not_override():
    screening = _screening()
    result = apply_article_77_3(screening, StandingCheck(consistent=True))
    assert result == screening


def test_apply_77_3_inconsistent_with_standing_flags_but_does_not_override():
    """不一致但有法律上利害關係,不覆寫,只標記爭點待確認。"""
    screening = _screening()
    result = apply_article_77_3(screening, StandingCheck(consistent=False, has_standing=True))
    assert result.passed is True
    assert result.review_note != ""


def test_apply_77_3_inconsistent_without_standing_overrides_to_third_clause():
    """不一致且無利害關係,覆寫為第3款不受理,但仍標記待人工確認(利害關係屬價值判斷)。"""
    screening = _screening()
    result = apply_article_77_3(screening, StandingCheck(consistent=False, has_standing=False))
    assert result.passed is False
    assert result.matched_clause == "77條第3款"
    assert result.review_note != ""


def test_apply_77_3_inconsistent_with_unknown_standing_does_not_override():
    """不一致但 LLM 判斷不出來(has_standing=None),不猜,不覆寫,只標記待人工認定。"""
    screening = _screening()
    result = apply_article_77_3(screening, StandingCheck(consistent=False, has_standing=None))
    assert result.passed is True
    assert result.review_note != ""


# ---------- resolve_standing_assessment:指不出保護規範就視同無法判斷 ----------


def test_resolve_standing_assessment_keeps_has_standing_when_norm_cited():
    """模型指出具體法規名稱＋條號時,has_standing 照原樣採用。"""
    assessment = StandingAssessment(referenced_norm="廢棄物清理法#27", has_standing=False)
    assert resolve_standing_assessment(assessment) is False


def test_resolve_standing_assessment_treats_blank_norm_as_unknown():
    """模型指不出保護規範(referenced_norm 空字串)時,即使 has_standing 有值也不採用——
    這是防「說得很順但沒有依據」的攔法,不是抽取層本身抽不到。"""
    assessment = StandingAssessment(referenced_norm="", has_standing=True)
    assert resolve_standing_assessment(assessment) is None


def test_resolve_standing_assessment_treats_norm_without_article_no_as_unknown():
    """只寫法規名稱、沒有條號(不含「#數字」格式),同樣視為指不出具體保護規範。"""
    assessment = StandingAssessment(referenced_norm="廢棄物清理法", has_standing=False)
    assert resolve_standing_assessment(assessment) is None


def test_resolve_standing_assessment_accepts_sub_article_number():
    """條號可含「-1」等細分款格式(如 44-1),不應被誤判為指不出條號。"""
    assessment = StandingAssessment(referenced_norm="行政罰法#44-1", has_standing=True)
    assert resolve_standing_assessment(assessment) is True
