"""抽取層以語料 21 件 §77(2) 決定書理由為驗收集:抽出的事實重算後須等於機關自述的末日。"""
import json
from datetime import date
from pathlib import Path

import pytest

from app.deadline import compute_deadline
from app.deadline_extract import extract_deadline_facts, extract_from_documents

_CORPUS = Path(__file__).parent / "corpus" / "decisions_77_2.jsonl"
_NO_HOLIDAY: frozenset[date] = frozenset()

# 語料中無法抽取的 2 件,值為 problem 必須提到的關鍵字——抽不到要說得出是哪一項抽不到
_UNEXTRACTABLE = {
    "1121090975": "送達日",  # 一案兩份裁處書,兩個送達日
    "TC1140845": "送達日",  # 同一函兩處送達(寄存郵局與接收郵件人員)
}
_NO_ARITHMETIC = "1141070443"  # 理由只寫送達日與收文日,未載在途期間也未列算式


def _rows():
    return [json.loads(line) for line in _CORPUS.read_text(encoding="utf-8").splitlines()]


_ROWS = _rows()


@pytest.mark.parametrize("row", _ROWS, ids=[r["case_no"] for r in _ROWS])
def test_corpus_reason_yields_facts_that_recompute_to_stated_due_date(row):
    result = extract_deadline_facts(row["reason"])
    if row["case_no"] in _UNEXTRACTABLE:
        assert result.facts is None
        assert _UNEXTRACTABLE[row["case_no"]] in result.problem
        return

    facts = result.facts
    assert facts is not None, result.problem
    if row["case_no"] == _NO_ARITHMETIC:
        assert facts.transit_days is None  # 未載即為 None,不得逕以 0 日計
        assert facts.stated_due_date is None
        return
    computed = compute_deadline(
        facts.service_date,
        holidays=_NO_HOLIDAY,
        period_days=facts.period_days,
        transit_days=facts.transit_days or 0,
    )
    assert computed.start_date == facts.stated_start_date
    assert computed.due_date == facts.stated_due_date
    if facts.filed_date is not None:  # 補正案無收文日,期間仍可算,逾期與否無從認定
        assert computed.is_overdue(facts.filed_date) is True


def test_deposit_service_date_is_the_deposit_day():
    """寄存送達:抽到的送達日就是寄存當日,不是實際領取日。"""
    facts = extract_deadline_facts(_by_case("1133091105")["reason"]).facts
    assert facts.service_date == date(2024, 7, 1)


def test_transit_days_read_from_the_reason_not_assumed():
    facts = extract_deadline_facts(_by_case("1141011503")["reason"]).facts
    assert facts.transit_days == 3


def test_correction_period_is_twenty_days_not_thirty():
    """補正期間 20 日與訴願期間 30 日共用同一算式,日數必須抽出來而非寫死。"""
    facts = extract_deadline_facts(_by_case("1121070633")["reason"]).facts
    assert facts.period_days == 20
    assert facts.filed_date is None  # 該案未補正,無收文日


def test_mention_of_the_transit_regulation_is_not_read_as_a_transit_finding():
    """「按訴願扣除在途期間辦法規定,無須扣除在途期間」須判為 0 日,不可被辦法名稱帶偏。"""
    facts = extract_deadline_facts(_by_case("TC1140896")["reason"]).facts
    assert facts.transit_days == 0


def test_empty_text_reports_a_problem_instead_of_guessing():
    result = extract_deadline_facts("")
    assert result.facts is None
    assert result.problem


def _by_case(case_no):
    return next(r for r in _ROWS if r["case_no"] == case_no)


def test_public_notice_service_waits_twenty_days_and_is_flagged():
    """公示送達自公告次日起算 20 日屆滿才生效(行政程序法§81),且一律標記待人工確認。"""
    text = "系爭處分書依法辦理公示送達,於110年9月17日公告張貼於本府公告欄。訴願人住居所位於本市,無須扣除在途期間。"

    facts = extract_deadline_facts(text).facts

    assert facts.service_date == date(2021, 10, 7)
    assert facts.public_notice is True


def test_public_notice_variant_wording_is_refused_not_guessed():
    """§78 I③與§79 的等待期不同,而條號恆出現在引述條文裡,分不清就不算。"""
    text = "因於外國或境外送達不能依規定辦理,爰依第78條第1項第3款為公示送達,於110年9月17日公告。無須扣除在途期間。"

    result = extract_deadline_facts(text)

    assert result.facts is None
    assert "種類無法認定" in result.problem


def test_residence_is_extracted_for_the_transit_table():
    """住居所要抽出來,才有辦法查在途期間對照表。"""
    facts = extract_deadline_facts(_by_case("1141011503")["reason"]).facts

    assert "桃園市" in facts.residence


_APPEAL_FORM = """訴　　願　　書
訴願人 姓名 王大明 住址 新北市板橋區中山路一段161號
原處分機關 新北市政府環境保護局
處分書發文日期及字號 114年5月16日新北環稽字第22-114-050050號
附註 收受或知悉行政處分日期：114年5月28日
中華民國114年10月31日"""

_SERVICE_CERTIFICATE = """新北市政府環境保護局 送達證書
受送達人名稱姓名地址 新北市板橋區中山路一段161號
送達處所（由送達人填記）同上記載地址
送達時間（由送達人填記）中華民國114年5月28日上午10時30分
送達方式 未獲會晤本人，已將文書交與應送達處所之接收郵件人員"""


def test_appeal_form_fields_are_read_by_their_official_labels():
    """新北市法制局訴願書範本的欄位名:送達日在「收受或知悉行政處分日期」,地址欄名為「住址」。"""
    facts = extract_deadline_facts(_APPEAL_FORM).facts

    assert facts.service_date == date(2025, 5, 28)
    assert "板橋區" in facts.residence


def test_disposition_issue_date_is_not_taken_as_the_service_date():
    """同一份訴願書上還有處分書發文日期,抽錯這個會把期間整段往前挪。"""
    facts = extract_deadline_facts(_APPEAL_FORM).facts

    assert facts.service_date != date(2025, 5, 16)


def test_service_certificate_fields_are_read_by_their_official_labels():
    """法務部送達證書範本沒有「送達日期」欄,日期寫在「送達時間」格內。"""
    facts = extract_deadline_facts(_SERVICE_CERTIFICATE).facts

    assert facts.service_date == date(2025, 5, 28)
    assert "板橋區" in facts.residence


# ---------- extract_from_documents:分槽抽取 ----------


def test_from_documents_consistent_dates_yield_facts():
    """訴願書自述收受日與送達證書送達時間一致時,正常產出 facts,不因分槽而多報問題。"""
    result = extract_from_documents(_APPEAL_FORM, _SERVICE_CERTIFICATE)

    assert result.facts is not None
    assert result.facts.service_date == date(2025, 5, 28)
    assert result.problem == ""


def test_from_documents_conflicting_dates_take_the_certificate_and_record_the_dispute():
    """送達證書為公文書,推定真正,故送達生效日以其為準;訴願書自述的收受或知悉日另記為爭點。
    不整筆棄權——訴願人主張未收受正是 77(2) 要判的事,停在這裡等於不判。"""
    conflicting_service = _SERVICE_CERTIFICATE.replace("114年5月28日", "114年6月2日")

    result = extract_from_documents(_APPEAL_FORM, conflicting_service)

    assert result.problem == ""
    assert result.facts.service_date == date(2025, 6, 2)
    assert result.facts.disputed_receipt_date == date(2025, 5, 28)


def test_from_documents_missing_service_slot_falls_back_to_appeal_self_report():
    """送達證書槽缺(卷內未附,§76 本為「得」製作)時退用訴願人自述日,但要標記未經核對。"""
    result = extract_from_documents(_APPEAL_FORM, "")

    assert result.facts is not None
    assert result.facts.service_date == date(2025, 5, 28)
    assert result.facts.service_date_self_reported is True
    assert result.facts.service_fallback_reason == "absent_slot"  # 槽內確實沒有這份文書


def test_from_documents_service_slot_present_but_missing_field_reason():
    """送達證書有文字,但抽不到送達時間欄——三態之一,不同於槽內完全沒有文書。"""
    service_without_date = "新北市政府環境保護局 送達證書\n受送達人名稱姓名地址 新北市板橋區中山路一段161號"

    result = extract_from_documents(_APPEAL_FORM, service_without_date)

    assert result.facts.service_fallback_reason == "missing_field"


def test_from_documents_unreadable_service_slot_reason():
    """送達證書有文字但無法辨識(可讀字元比例過低,與 OCR 的可讀性門檻共用同一套判準)。"""
    garbled_service = "\ufffd\ufffd\ufffd\ufffd※★●◆■" * 20

    result = extract_from_documents(_APPEAL_FORM, garbled_service)

    assert result.facts.service_fallback_reason == "unreadable"


def test_three_service_fallback_review_notes_are_distinct():
    """三種 review_note 文字彼此不同,不可共用一句。"""
    from app.pipeline import check_deadline_from_case
    from app.models import Case, CaseDocument

    def _case(service_text: str) -> Case:
        return Case(
            case_id="c-test",
            created_at="2025-01-01T00:00:00Z",
            title="測試",
            source="text",
            input_text="",
            documents={
                "appeal": CaseDocument(slot="appeal", source="text", text=_APPEAL_FORM),
                "service": CaseDocument(slot="service", source="text", text=service_text),
            },
        )

    absent = check_deadline_from_case(_case(""))
    missing_field = check_deadline_from_case(
        _case("新北市政府環境保護局 送達證書\n受送達人名稱姓名地址 新北市板橋區中山路一段161號")
    )
    unreadable = check_deadline_from_case(_case("\ufffd\ufffd\ufffd\ufffd※★●◆■" * 20))

    notes = {absent.review_note, missing_field.review_note, unreadable.review_note}
    assert len(notes) == 3  # 三者互不相同
    assert "卷內無送達證書" in absent.review_note
    assert "未載送達時間" in missing_field.review_note
    assert "無法辨識" in unreadable.review_note and "調閱原件" in unreadable.review_note


def test_from_documents_disposition_issue_date_in_appeal_text_not_mistaken_as_service_date():
    """訴願書上同時有處分書發文日期,分槽抽取一樣不能把它當成送達日——沿用原有的防呆規則。"""
    result = extract_from_documents(_APPEAL_FORM, _SERVICE_CERTIFICATE)

    assert result.facts.service_date != date(2025, 5, 16)


def test_from_documents_empty_appeal_and_service_reports_problem():
    result = extract_from_documents("", "")

    assert result.facts is None
    assert result.problem


_APPEAL_FORM_TABLE = """訴    願    書
訴願人
住址
聯絡電話

新北市樹林區裕昌街467 巷20 弄28 號
0912-345-678
收受或知悉行政處分日期：中華民國114 年9 月20 日"""


def test_residence_is_not_taken_from_the_adjacent_field_label():
    """表格版面的 PDF 先輸出整列欄名再輸出整列值,「住址」的下一行是「聯絡電話」不是地址。"""
    facts = extract_deadline_facts(_APPEAL_FORM_TABLE).facts

    assert facts is not None
    assert "樹林區" in facts.residence


def test_receipt_date_written_with_the_republic_era_prefix_is_read():
    """訴願書範本的日期格寫「中華民國114 年9 月20 日」,冒號與數字之間隔著國號。"""
    facts = extract_deadline_facts(_APPEAL_FORM_TABLE).facts

    assert facts is not None
    assert facts.service_date == date(2025, 9, 20)


def test_agency_receipt_stamp_written_with_the_republic_era_prefix_is_read():
    """訴願書上的機關收文戳寫「收文日期：中華民國114年9月25日」,國號夾在冒號與數字之間。
    抽不到收文日就只算得出末日、算不出是否逾期,§77(2) 等於沒判。"""
    text = _APPEAL_FORM_TABLE + "\n新北市政府環境保護局 收文\n收文日期：中華民國114年9月25日"

    facts = extract_deadline_facts(text).facts

    assert facts is not None
    assert facts.filed_date == date(2025, 9, 25)


def test_agency_receipt_stamp_wins_over_narrative_dates_in_the_appeal():
    """訴願法§14 III:提起日以機關收受訴願書之日為準。訴願書內文另有「於X日始至郵局領取」
    這類敘述,兩者不同不得整筆作廢——收文戳是法定判準,敘述只是備援。"""
    text = (
        _APPEAL_FORM_TABLE
        + "\n三、訴願人於112年2月13日始至郵局領取該文書。"
        + "\n新北市政府環境保護局 收文\n收文日期：中華民國112年3月15日"
    )

    facts = extract_deadline_facts(text).facts

    assert facts is not None
    assert facts.filed_date == date(2023, 3, 15)


def test_a_fallback_argument_is_not_part_of_the_stated_period():
    """訴願書會在主張之後追加備位主張(「退步言之…至遲亦應自C起算」)。備位主張裡的起算日
    不是卷內自述的末日,收進來會與算式對不上而生出一個假的待確認註記,把正確的算式壓住。"""
    text = (
        "原處分書於112年2月7日送達。"
        "本件寄存送達應自寄存之日起經十日始發生送達效力,訴願期間應自112年2月18日起算,"
        "訴願人住居所位於本市,無在途期間可資扣除,其30日訴願期間至112年3月19日屆滿。"
        "退步言之,訴願人實際知悉處分內容之日為112年2月13日,訴願期間至遲亦應自112年2月14日起算。"
    )
    result = extract_deadline_facts(text)
    facts = result.facts
    assert facts is not None, result.problem

    assert facts.stated_start_date == date(2023, 2, 18)
    assert facts.stated_due_date == date(2023, 3, 19)
