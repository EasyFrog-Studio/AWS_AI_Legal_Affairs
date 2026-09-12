"""app.dates 的正規化行為:標準寫法「民國114年7月4日」的唯一入口。"""
import pytest

from datetime import date

from app.dates import normalize_case_info_dates, normalize_roc, parse_roc
from app.models import CaseInfo


@pytest.mark.parametrize(
    "text",
    [
        "114年7月4日",
        "民國114年7月4日",
        "中華民國 114 年 7 月 4 日",
        "114.7.4",
        "114/07/04",
        "2025-07-04",
    ],
)
def test_normalize_roc_accepts_common_writings(text):
    assert normalize_roc(text) == "民國114年7月4日"


def test_normalize_roc_drops_time_of_day():
    assert normalize_roc("114年7月9日10時30分") == "民國114年7月9日"


@pytest.mark.parametrize("text", ["未載明", "", "114年2月30日"])
def test_normalize_roc_returns_none_for_unparseable_or_missing_text(text):
    assert normalize_roc(text) is None


@pytest.mark.parametrize("text", ["民國114年7月4日", "2025-07-04", "114/7/4"])
def test_parse_roc_returns_a_date_for_common_writings(text):
    assert parse_roc(text) == date(2025, 7, 4)


@pytest.mark.parametrize("text", ["看不懂的日期", None, "114年2月30日"])
def test_parse_roc_returns_none_for_unparseable_text(text):
    assert parse_roc(text) is None


def _info(**overrides) -> CaseInfo:
    base = dict(
        appellant="王大明",
        agency="彰化縣環境保護局",
        disposition_date="110年3月5日",
        disposition_no="彰環廢字第1號",
        disposition_summary="裁處罰鍰",
        case_type="廢棄物清理",
    )
    base.update(overrides)
    return CaseInfo(**base)


def test_normalize_case_info_dates_rewrites_a_parseable_field():
    info = _info(receipt_date="114年7月4日")
    normalized = normalize_case_info_dates(info)
    assert normalized.receipt_date == "民國114年7月4日"


def test_normalize_case_info_dates_keeps_unparseable_field_as_is():
    """解析不出的保留原文,前端據此顯示「無法辨識」——不是覆寫成空字串或報錯。"""
    info = _info(service_date="114年2月30日")
    normalized = normalize_case_info_dates(info)
    assert normalized.service_date == "114年2月30日"


def test_normalize_case_info_dates_does_not_touch_non_date_fields():
    info = _info(appellant="王大明", case_type="廢棄物清理")
    normalized = normalize_case_info_dates(info)
    assert normalized.appellant == "王大明"
    assert normalized.case_type == "廢棄物清理"


def test_normalize_case_info_dates_handles_multiple_heterogeneous_fields_at_once():
    """兩組以上異質輸入同時存在:可解析欄改寫、不可解析欄保留、日期欄清單以外的仍不受影響。"""
    info = _info(
        receipt_date="114.7.4",
        appeal_date="民國114年7月9日",
        service_date="未載明",
        disposition_no="彰環廢字第1號",
    )
    normalized = normalize_case_info_dates(info)
    assert normalized.receipt_date == "民國114年7月4日"
    assert normalized.appeal_date == "民國114年7月9日"  # 已是標準寫法,不變
    assert normalized.service_date == "未載明"  # 解析不出,保留原文
    assert normalized.disposition_no == "彰環廢字第1號"  # 非日期欄,不動
