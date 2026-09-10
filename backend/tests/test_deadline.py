"""期間計算測資全部取自語料 §77(2) 案決定書理由中機關自己列的算式,非自擬案例。"""
from datetime import date
from typing import NamedTuple

import pytest

from app.deadline import compute_deadline, compute_one_year_deadline, public_notice_service_date

_NO_HOLIDAY: frozenset[date] = frozenset()  # 語料 19 件末日只涉及週六日,未觸及國定假日
_WEEKDAY_DUE_SERVICE = date(2025, 5, 12)  # 其末日 2025-06-11 為星期三,可單獨檢驗假日順延


class Row(NamedTuple):
    """一件決定書理由所載的完整算式:前三欄為輸入,後四欄為機關自己算出的結果。"""

    case_no: str
    service: date  # 送達生效日
    transit: int  # 在途期間日數
    start: date  # 起算日
    original_due: date  # 順延前末日
    due: date  # 末日
    filed: date  # 機關收文日


_OVERDUE_CASES = [
    Row("1146051286", date(2025, 7, 4), 0,
        date(2025, 7, 5), date(2025, 8, 3), date(2025, 8, 4), date(2025, 9, 25)),
    Row("1133091105", date(2024, 7, 1), 0,  # 寄存
        date(2024, 7, 2), date(2024, 7, 31), date(2024, 7, 31), date(2024, 8, 6)),
    Row("1141021559", date(2025, 5, 28), 0,
        date(2025, 5, 29), date(2025, 6, 27), date(2025, 6, 27), date(2025, 10, 31)),
    Row("1141021453", date(2025, 4, 1), 5,  # 寄存
        date(2025, 4, 2), date(2025, 5, 6), date(2025, 5, 6), date(2025, 10, 9)),
    Row("1141011430", date(2025, 8, 25), 2,
        date(2025, 8, 26), date(2025, 9, 26), date(2025, 9, 26), date(2025, 10, 1)),
    Row("1141121313", date(2025, 6, 2), 0,
        date(2025, 6, 3), date(2025, 7, 2), date(2025, 7, 2), date(2025, 9, 11)),
    Row("1141011503", date(2025, 1, 15), 3,  # 寄存
        date(2025, 1, 16), date(2025, 2, 17), date(2025, 2, 17), date(2025, 10, 23)),
    Row("1143071054", date(2025, 4, 7), 0,
        date(2025, 4, 8), date(2025, 5, 7), date(2025, 5, 7), date(2025, 7, 24)),
    Row("1143070708", _WEEKDAY_DUE_SERVICE, 0,
        date(2025, 5, 13), date(2025, 6, 11), date(2025, 6, 11), date(2025, 6, 17)),
    Row("TC1140913", date(2024, 11, 2), 3,
        date(2024, 11, 3), date(2024, 12, 5), date(2024, 12, 5), date(2025, 10, 23)),
    Row("TC1140896", date(2025, 6, 5), 0,  # 末日週六
        date(2025, 6, 6), date(2025, 7, 5), date(2025, 7, 7), date(2025, 10, 21)),
    Row("TC1140975", date(2025, 2, 24), 3,  # 在途加完才順延
        date(2025, 2, 25), date(2025, 3, 29), date(2025, 3, 31), date(2025, 11, 7)),
    Row("TC1140820", date(2025, 3, 16), 5,
        date(2025, 3, 17), date(2025, 4, 20), date(2025, 4, 21), date(2025, 9, 24)),
    Row("1121090975-1", date(2019, 4, 25), 0,  # 裁處書仍順延
        date(2019, 4, 26), date(2019, 5, 25), date(2019, 5, 27), date(2023, 8, 14)),
    Row("1121090975-2", date(2020, 7, 22), 0,
        date(2020, 7, 23), date(2020, 8, 21), date(2020, 8, 21), date(2023, 8, 14)),
    Row("1141010955", date(2024, 1, 3), 0,
        date(2024, 1, 4), date(2024, 2, 2), date(2024, 2, 2), date(2025, 7, 18)),
    Row("1147090926", date(2025, 1, 20), 0,
        date(2025, 1, 21), date(2025, 2, 19), date(2025, 2, 19), date(2025, 7, 15)),
    Row("1149100561", date(2025, 4, 2), 0,
        date(2025, 4, 3), date(2025, 5, 2), date(2025, 5, 2), date(2025, 5, 9)),
    Row("1139090350", date(2024, 2, 2), 0,  # 寄存
        date(2024, 2, 3), date(2024, 3, 3), date(2024, 3, 4), date(2024, 3, 6)),
]


@pytest.mark.parametrize("row", _OVERDUE_CASES, ids=[r.case_no for r in _OVERDUE_CASES])
def test_matches_corpus_arithmetic(row):
    result = compute_deadline(row.service, holidays=_NO_HOLIDAY, transit_days=row.transit)
    assert result.start_date == row.start
    assert result.original_due_date == row.original_due
    assert result.due_date == row.due
    assert result.is_overdue(row.filed) is True  # 語料 19 件全數經機關認定逾期


def test_correction_period_of_twenty_days():
    """訴願法§62 補正期間 20 日(語料 1121070633),同一算式僅期間長度不同。"""
    result = compute_deadline(date(2023, 6, 21), holidays=_NO_HOLIDAY, period_days=20)
    assert result.due_date == date(2023, 7, 11)


def test_deposit_service_effective_on_deposit_day_not_ten_days_later():
    """行政程序法§74 寄存送達以寄存當日生效(釋字797號,語料 1139090350),不加 10 日。"""
    result = compute_deadline(date(2024, 2, 2), holidays=_NO_HOLIDAY)
    assert result.due_date == date(2024, 3, 4)
    assert result.due_date != date(2024, 3, 14)


def test_filing_on_due_date_is_timely():
    result = compute_deadline(_WEEKDAY_DUE_SERVICE, holidays=_NO_HOLIDAY)
    assert result.is_overdue(date(2025, 6, 11)) is False
    assert result.is_overdue(date(2025, 6, 12)) is True


def test_due_date_on_supplied_holiday_shifts_to_next_working_day():
    """國定假日須由呼叫端提供,函式無從自行判斷。"""
    result = compute_deadline(_WEEKDAY_DUE_SERVICE, holidays={date(2025, 6, 11)})
    assert result.original_due_date == date(2025, 6, 11)
    assert result.due_date == date(2025, 6, 12)


def test_consecutive_holidays_and_weekend_are_skipped_together():
    """連假接週末須一路順延到下一個上班日,不可只跳一天。"""
    result = compute_deadline(
        _WEEKDAY_DUE_SERVICE, holidays={date(2025, 6, 11), date(2025, 6, 12), date(2025, 6, 13)}
    )
    assert result.due_date == date(2025, 6, 16)  # 6/14 週六、6/15 週日


def test_holidays_is_required_keyword():
    """漏傳假日會安靜算錯,故不給預設值。"""
    with pytest.raises(TypeError):
        compute_deadline(_WEEKDAY_DUE_SERVICE)


def test_public_notice_effective_dates_follow_article_81():
    """行政程序法§81:本文 20 日、§78 I③ 於外國或境外 60 日、§79 職權公示送達為公告次日。"""
    posted = date(2021, 9, 17)
    assert public_notice_service_date(posted) == date(2021, 10, 7)
    assert public_notice_service_date(posted, abroad=True) == date(2021, 11, 16)
    assert public_notice_service_date(posted, repeat=True) == date(2021, 9, 18)


# ---------- compute_one_year_deadline:行政程序法§98 III 未教示/未更正的一年期間 ----------


def test_one_year_deadline_is_the_day_before_next_year_anniversary_of_start_date():
    """始日不計,起算日次年同月同日之前一日為末日(民法§121 II 類推)。"""
    result = compute_one_year_deadline(date(2024, 5, 28), holidays=frozenset())
    assert result.start_date == date(2024, 5, 29)
    assert result.due_date == date(2025, 5, 28)


def test_one_year_deadline_handles_leap_day_start():
    """起算日恰為2/29,次年無對應日,退回2/28。"""
    result = compute_one_year_deadline(date(2024, 2, 28), holidays=frozenset())  # 起算日 2024-02-29
    assert result.start_date == date(2024, 2, 29)
    assert result.due_date == date(2025, 2, 28)


def test_one_year_deadline_shifts_over_weekend():
    """末日遇週六日仍依§48 IV 順延。"""
    # 2025-05-28 為星期三，改用一個確定落在週末的例子
    result = compute_one_year_deadline(date(2024, 5, 24), holidays=frozenset())
    assert result.original_due_date.weekday() in (5, 6)
    assert result.due_date.weekday() < 5
