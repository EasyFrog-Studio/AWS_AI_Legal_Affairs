"""外部對照資料的涵蓋度回報:讓「假日表過期了」有觸發點,見實作計畫 Ticket 14。"""
from datetime import date

from app.reference_data import reference_data_status


def test_reports_the_covered_year_range_of_the_holiday_table():
    status = reference_data_status(today=date(2026, 8, 17))

    holidays = status["holidays"]
    assert holidays["min_year"] == 2017
    assert holidays["max_year"] == 2027  # 人事行政總處辦公日曆表目前公布至民國116年
    assert holidays["source"]


def test_reports_the_transit_table_source():
    status = reference_data_status(today=date(2026, 8, 17))

    assert "law.moj.gov.tw" in status["transit_days"]["source"]
    assert status["transit_days"]["agency_rows"] > 0


def test_no_warning_while_the_table_covers_next_year():
    """今年 +1 已收錄就沒事:末日落在已收錄年度,順延判斷才是完整的。"""
    assert reference_data_status(today=date(2026, 8, 17))["warning"] == ""


def test_warns_when_the_table_does_not_reach_next_year():
    """收錄年度未及今年 +1 就該重抓——不是等到算錯才發現。"""
    warning = reference_data_status(today=date(2027, 1, 1))["warning"]

    assert "2028" in warning
    assert "fetch_holidays.py" in warning  # 講出該跑哪一支,不只說「資料過期」


def test_warns_when_the_holiday_table_is_empty(monkeypatch):
    """檔案不存在時 load_holidays 回空集合,那不等於「該年沒有假日」,必須出警告。"""
    import app.reference_data as module

    monkeypatch.setattr(module, "covered_years", lambda: frozenset())

    warning = module.reference_data_status(today=date(2026, 8, 17))["warning"]

    assert "未收錄" in warning
