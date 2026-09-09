"""外部對照資料(國定假日表、在途期間附表)的涵蓋狀態,供 /api/health 回報。

期間計算的正確性靠這兩份表,但它們會過期:辦公日曆表由人事行政總處逐年公布,末日落在
未收錄年度時 compute_deadline 只能標 review_note。那道網是對的,但沒有任何地方告訴維護者
「該重抓了」——這支模組就是那個觸發點(見實作計畫 Ticket 14)。
"""
from datetime import date
from typing import Optional

from app.holidays import covered_years
from app.transit import table_source, agency_count

# preprocessing/fetch_holidays.py 的抓取來源,重抓時要去的地方
HOLIDAY_SOURCE = "https://data.gov.tw/api/v2/rest/dataset/14718"  # 中華民國政府行政機關辦公日曆表
_FETCH_SCRIPT = "preprocessing/fetch_holidays.py"


def reference_data_status(today: Optional[date] = None) -> dict:
    """兩份對照資料的涵蓋狀態 + warning。warning 為空字串代表兩份表都夠新;
    非空即代表算得出來的末日可能沒有把假日順延計入,須重抓。"""
    current = today or date.today()
    years = covered_years()
    needed = current.year + 1  # 今年的案子末日可能落在明年,故明年也必須已收錄

    if not years:
        warning = f"國定假日表未收錄任何年度,末日是否須順延無從判斷,請執行 {_FETCH_SCRIPT} 重抓"
    elif max(years) < needed:
        warning = (
            f"國定假日表僅收錄至 {max(years)} 年,未及 {needed} 年,"
            f"請執行 {_FETCH_SCRIPT} 重抓"
        )
    else:
        warning = ""

    return {
        "holidays": {
            "min_year": min(years) if years else None,
            "max_year": max(years) if years else None,
            "source": HOLIDAY_SOURCE,
        },
        "transit_days": {
            "source": table_source(),
            "agency_rows": agency_count(),
        },
        "warning": warning,
    }
