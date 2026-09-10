"""教示條款 -> 行政程序法§98 分支的認定。"""
from datetime import date

from app.notice_clause import classify_notice_clause

_FULL = "如不服本處分,得於本處分書送達之次日起三十日內,繕具訴願書向本府提起訴願。"
_DISPOSITION = "主旨:裁處罰鍰新臺幣6,000元。事實及理由:違反廢棄物清理法第27條規定。"


def test_complete_notice_clause_stays_on_the_statutory_period():
    """教示完整且期間等於法定 30 日:§98 不介入,也不留待人工記號。"""
    rule = classify_notice_clause(_FULL, f"{_DISPOSITION}{_FULL}")

    assert rule.basis == "statutory"
    assert rule.stated_days == 30
    assert rule.review_note == ""


def test_arabic_numeral_notice_clause_is_also_recognised():
    """機關寫「30日內」與寫「三十日內」是同一件事,不可因寫法差異判成未告知。"""
    rule = classify_notice_clause("得於送達次日起30日內提起訴願。", _DISPOSITION)

    assert rule.basis == "statutory"


def test_missing_notice_clause_switches_to_the_one_year_period():
    """§98 III:未告知救濟期間 -> 自處分書送達後一年。原處分書在手才敢下這個結論。"""
    rule = classify_notice_clause("", _DISPOSITION)

    assert rule.basis == "one_year"
    assert "未告知救濟期間" in rule.finding
    assert "第98條第3項" in rule.review_note
    assert rule.review_note != ""  # 一律待人工確認


def test_notice_clause_without_a_period_counts_as_no_notice():
    """有救濟教示句但沒寫期間,仍屬「未告知救濟期間」,不是教示完整。"""
    rule = classify_notice_clause("如不服本處分,得繕具訴願書向本府提起訴願。", _DISPOSITION)

    assert rule.basis == "one_year"


def test_truncated_clause_field_falls_back_to_the_disposition_text():
    """模型把教示條款抽斷了,不可因此判成未告知——退回原處分書全文再找一次。"""
    rule = classify_notice_clause("如不服本處分,", f"{_DISPOSITION}{_FULL}")

    assert rule.basis == "statutory"
    assert rule.stated_days == 30


def test_longer_stated_period_is_honoured():
    """§98 II:告知期間較法定期間長 -> 依原告知之期間。"""
    rule = classify_notice_clause("得於送達次日起六十日內提起訴願。", _DISPOSITION)

    assert rule.basis == "stated_longer"
    assert rule.stated_days == 60
    assert "第98條第2項" in rule.review_note


def test_corrected_wrong_period_restarts_from_the_correction_notice():
    """§98 I:告知錯誤且已通知更正 -> 自更正通知送達之翌日起算法定期間。"""
    rule = classify_notice_clause(
        "得於送達次日起二十日內提起訴願。",
        _DISPOSITION,
        case_text="原處分機關就救濟期間錯誤另以更正通知於114年6月10日送達訴願人。",
    )

    assert rule.basis == "restart_from_correction"
    assert rule.correction_service_date == date(2025, 6, 10)
    assert "第98條第1項" in rule.review_note


def test_wrong_period_recorded_as_uncorrected_goes_to_the_one_year_period():
    """卷內明載未為更正,才走§98 III 的一年——這是「明載」而非「沒看到」。"""
    rule = classify_notice_clause(
        "得於送達次日起二十日內提起訴願。",
        _DISPOSITION,
        case_text="原處分機關告知之救濟期間有誤,迄未通知更正。",
    )

    assert rule.basis == "one_year"
    assert "未為更正" in rule.finding


def test_wrong_period_without_any_correction_record_says_it_cannot_tell():
    """有無通知更正抽不到時明說抽不到,不以「無更正通知」頂替去換一年期間。"""
    rule = classify_notice_clause("得於送達次日起二十日內提起訴願。", _DISPOSITION)

    assert rule.basis == "undetermined"
    assert "不得逕認未為更正" in rule.review_note
    assert "第98條第1項或第3項" in rule.review_note


def test_correction_notice_without_a_service_date_is_undetermined():
    """卷內載有更正通知卻查不到其送達日:§98 I 的起算日無從認定,不可拿原處分送達日頂替。"""
    rule = classify_notice_clause(
        "得於送達次日起二十日內提起訴願。",
        _DISPOSITION,
        case_text="原處分機關已另發更正通知。",
    )

    assert rule.basis == "undetermined"
    assert rule.correction_service_date is None
    assert "更正通知" in rule.review_note


def test_no_disposition_document_is_reported_as_not_checked():
    """卷內沒有原處分書就無從認定有無教示;不阻斷期間計算,但不可靜默當作教示完整。"""
    rule = classify_notice_clause("", "")

    assert rule.basis == "not_assessed"
    assert rule.review_note != ""
