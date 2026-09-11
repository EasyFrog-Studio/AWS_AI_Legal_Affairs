"""開箱素材(data_show/examples)的守門測試。

這些 PDF 是「打開網站就有東西可以跑完整條流程」的唯一保證,所以它們必須:
規則層就認得出三個槽位(無 Gemini 金鑰)、mock 樣本比對得到、期間算得出末日。
測試直接讀已產生的 PDF(不是讀 gen_examples.py 的字串常數)——要驗的是使用者真的會拖進
網站的那份檔案,PDF 的文字層有沒有壞掉本身就是這條測試的一部分(內建 CJK 字型會抽出亂碼)。
"""
from datetime import date
from pathlib import Path

import pytest

from app.config import settings
from app.deadline_extract import extract_from_documents
from app.document_check import check_document
from app.models import CaseDocument, DocumentCheck, build_input_text
from app.pdf_extract import extract_text_quality
from app.pipeline import check_deadline_from_case, reconcile_deadline
from app.providers.mock import MockProvider

_EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "data_show" / "examples"
_SLOT_FILES = {"appeal": "01_訴願書.pdf", "service": "02_送達證書.pdf", "disposition": "03_原處分書.pdf"}
_DEPOSIT_CASE = "範例1_不受理_寄存送達逾期"
_DISMISS_CASE = "範例2_受理駁回_廢棄物清理法"


def _slot_text(case_dir: str, slot: str) -> str:
    path = _EXAMPLES_DIR / case_dir / _SLOT_FILES[slot]
    assert path.is_file(), f"開箱素材缺檔:{path}(執行 data_show/examples/gen_examples.py 產生)"
    return extract_text_quality(path.read_bytes()).text


def _case_texts(case_dir: str) -> dict[str, str]:
    return {slot: _slot_text(case_dir, slot) for slot in _SLOT_FILES}


def _case(case_dir: str):
    from app.models import Case

    documents = {
        slot: CaseDocument(slot=slot, source="pdf", text=text, check=DocumentCheck(matched=True, method="rule"))
        for slot, text in _case_texts(case_dir).items()
    }
    return Case(
        case_id="c-demo0001",
        created_at="2026-08-17T00:00:00+00:00",
        title="開箱示範案",
        source="pdf",
        input_text=build_input_text(documents),
        documents=documents,
    )


@pytest.mark.parametrize("case_dir", [_DEPOSIT_CASE, _DISMISS_CASE])
@pytest.mark.parametrize("slot", list(_SLOT_FILES))
def test_every_demo_document_is_recognised_by_the_rule_layer(case_dir, slot, monkeypatch):
    """無 Gemini 金鑰時規則層就要判得出來,否則開箱即用會停在「無法確認」而按不下開始分析。"""
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")

    check = check_document(slot, _slot_text(case_dir, slot))

    assert check.matched is True, f"{case_dir}/{slot}: {check.note}"
    assert check.method == "rule"


@pytest.mark.parametrize("case_dir", [_DEPOSIT_CASE, _DISMISS_CASE])
def test_demo_documents_have_a_real_text_layer(case_dir):
    """PyMuPDF 內建 china-t 沒有 ToUnicode CMap,抽出來會是亂碼——那種 PDF 過不了這一條。"""
    for slot in _SLOT_FILES:
        quality = extract_text_quality((_EXAMPLES_DIR / case_dir / _SLOT_FILES[slot]).read_bytes())
        assert quality.char_count > 100
        assert quality.readable_ratio > 0.9


@pytest.mark.parametrize(
    "case_dir,appellant",
    [(_DEPOSIT_CASE, "吳○彰"), (_DISMISS_CASE, "陳○瑤")],
)
def test_mock_provider_matches_the_intended_sample(case_dir, appellant):
    """三槽合併後的文字仍要命中對應樣本(姓名/機關/案由各 1 分,需 >= 2 分),
    否則 demo 會落到 _fallback,畫面上看起來有結果但其實是保底回應。"""
    provider = MockProvider()
    merged = build_input_text(
        {
            slot: CaseDocument(slot=slot, source="pdf", text=text)
            for slot, text in _case_texts(case_dir).items()
        }
    )

    info = provider.extract_case_info(merged)

    assert info.appellant == appellant
    assert info.receipt_date  # 六個新欄位在樣本裡有值,前端的期間複核才看得到東西
    assert info.disposition_notice_clause


def test_deposit_demo_case_computes_an_overdue_due_date():
    """寄存送達案:卷內日期算得出末日,而且算式本身可逕採(review_note 為空),
    reconcile_deadline 才會把結論覆寫成第2款——這是 demo 能跑到不受理草稿的前提。"""
    case = _case(_DEPOSIT_CASE)

    check = check_deadline_from_case(case, info=MockProvider().extract_case_info(case.input_text))

    assert check.service_date == date(2021, 9, 20)  # 寄存當日即生效,不加 10 日
    assert check.due_date == date(2021, 10, 20)
    assert check.overdue is True
    assert check.review_note == ""


def test_dismiss_demo_case_is_timely_and_stays_admissible():
    """受理案:末日 110/11/27 為週六順延至 11/29,收文日 11/15 未逾期,結論不得被期間翻掉。"""
    from app.models import ScreeningResult

    case = _case(_DISMISS_CASE)
    check = check_deadline_from_case(case, info=MockProvider().extract_case_info(case.input_text))
    screening = ScreeningResult(passed=True, matched_clause=None, reasoning="無不受理事由")

    result, reconciled = reconcile_deadline(screening, check)

    assert check.due_date == date(2021, 11, 29)
    assert check.overdue is False
    assert result.passed is True
    assert reconciled.review_note == ""


# ---------- 送達證書的兩種變體:把「拿不到實件」這個未知圈成有界 ----------

_VARIANT_DIR = _EXAMPLES_DIR / "送達證書變體"
_APPEAL_SELF_REPORTED = "收受或知悉行政處分日期：110年9月20日"


def _variant_text(name: str) -> str:
    path = _VARIANT_DIR / f"{name}.pdf"
    assert path.is_file(), f"變體素材缺檔:{path}"
    return extract_text_quality(path.read_bytes()).text


def test_service_date_written_outside_the_grid_is_still_extracted():
    """手填時常見:「送達時間」欄名與年月日不同行。抽得到就不該退用自述日。"""
    extraction = extract_from_documents(
        _APPEAL_SELF_REPORTED, _variant_text("變體1_送達證書_日期寫在格線外")
    )

    assert extraction.facts is not None, extraction.problem
    assert extraction.facts.service_date == date(2021, 9, 20)
    assert extraction.facts.service_date_self_reported is False


def test_postmark_only_certificate_reports_missing_instead_of_guessing():
    """交郵版最常見的實務作法:「送達時間」空白,日期只出現在郵局日戳。
    日戳與送達時間在法律上不是同一件事——不得拿日戳當送達時間(此處日戳刻意早兩天,
    猜值的話末日會整整差兩天)。"""
    extraction = extract_from_documents(
        _APPEAL_SELF_REPORTED, _variant_text("變體2_送達證書_僅有郵局日戳")
    )

    assert extraction.facts is not None, extraction.problem
    assert extraction.facts.service_date == date(2021, 9, 20)  # 退用訴願人自述日
    assert extraction.facts.service_date != date(2021, 9, 18)  # 不是日戳日
    assert extraction.facts.service_date_self_reported is True
    assert extraction.facts.service_fallback_reason == "missing_field"
