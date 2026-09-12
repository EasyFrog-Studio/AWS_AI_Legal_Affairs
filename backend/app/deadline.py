"""訴願期間計算:訴願法§14 起訖點、行政程序法§48 始日不計與末日順延、§81 公示送達等待期,以及民國紀年換算。"""
from collections.abc import Collection
from datetime import date, timedelta

from pydantic import BaseModel

APPEAL_PERIOD_DAYS = 30  # 訴願法§14 I;行政程序法§98 各項所稱「法定期間」在訴願案即指此,故公開共用
_ROC_YEAR_OFFSET = 1911  # 卷內日期一律民國紀年,抽取與書寫共用同一組換算


def roc_to_date(year: str, month: str, day: str) -> date:
    """民國年月日 -> date。"""
    return date(int(year) + _ROC_YEAR_OFFSET, int(month), int(day))


def format_roc(value: date) -> str:
    """date -> 民國紀年字串,決定書理由的日期一律這樣寫。"""
    return f"{value.year - _ROC_YEAR_OFFSET}年{value.month}月{value.day}日"


_FULLWIDTH_DIGITS = str.maketrans("0123456789", "０１２３４５６７８９")


def fullwidth(value: int | str) -> str:
    """期間敘述的數字一律全形(公文書體例);日期不轉,走 format_roc。"""
    return str(value).translate(_FULLWIDTH_DIGITS)


_PUBLIC_NOTICE_DAYS = 20  # 行政程序法§81 本文
_PUBLIC_NOTICE_ABROAD_DAYS = 60  # 同條:依§78 I③(於外國或境外)為公示送達者
_REPEAT_NOTICE_DAYS = 1  # 同條但書:§79 職權公示送達自黏貼公告欄翌日起生效


def public_notice_service_date(posted_date: date, *, abroad: bool = False, repeat: bool = False) -> date:
    """公示送達的生效日。期間依§48 II 始日不計,故自公告次日起算,末日即生效日。"""
    if repeat:
        return posted_date + timedelta(days=_REPEAT_NOTICE_DAYS)
    days = _PUBLIC_NOTICE_ABROAD_DAYS if abroad else _PUBLIC_NOTICE_DAYS
    return posted_date + timedelta(days=days)


class DeadlineResult(BaseModel):
    start_date: date  # 起算日:送達次日(行政程序法§48 II 始日不計算在內)
    original_due_date: date  # 順延前末日,決定書理由須一併載明
    due_date: date  # 末日:遇週六日或休息日依行政程序法§48 IV 順延

    def is_overdue(self, filed_date: date) -> bool:
        """末日當天提起仍屬合法,故逾期以嚴格大於認定。"""
        return filed_date > self.due_date


def compute_deadline(
    service_date: date,
    *,
    holidays: Collection[date],
    period_days: int = APPEAL_PERIOD_DAYS,
    transit_days: int = 0,
) -> DeadlineResult:
    """送達生效日 -> 期間末日。holidays 不給預設值:漏傳國定假日會算錯又不會報錯。"""
    # service_date 是送達生效日;寄存送達即寄存當日,不加 10 日
    start = service_date + timedelta(days=1)
    # 在途期間(訴願法§16)先加進末日,再判順延——語料 TC1140975 即依此順序
    original_due = start + timedelta(days=period_days - 1 + transit_days)
    due = original_due
    # §48 V 不利處分末日「照計」,但但書於對人民有利時回歸第4項,語料裁處書案仍順延,故一律順延
    while due.weekday() >= 5 or due in holidays:
        due += timedelta(days=1)
    return DeadlineResult(start_date=start, original_due_date=original_due, due_date=due)


def compute_one_year_deadline(service_date: date, *, holidays: Collection[date]) -> DeadlineResult:
    """行政程序法§98 III:處分機關未告知救濟期間,或告知錯誤未經更正,致相對人遲誤者,
    自處分書送達後**一年內**聲明不服視為於法定期間內所為。語料零件,見 pipeline.py 的呼叫端
    一律不覆寫程序審查——這裡只負責把「一年」這件事算成具體日期,供 review_note 顯示。

    始日不計(§48 II):起算日為送達次日;以年計算期間者,「與起算日相當之日之前一日」為期間末日
    (民法第121條第2項),故以「起算日」(不是送達日本身)推下一年同月同日,再退一日。
    起算日恰為2/29 時次年無對應日,依同條例外退回2/28(比照週年方式)。末日遇假日仍依§48 IV 順延。
    """
    start = service_date + timedelta(days=1)
    try:
        anniversary = date(start.year + 1, start.month, start.day)
    except ValueError:
        anniversary = date(start.year + 1, 3, 1)
    original_due = anniversary - timedelta(days=1)
    due = original_due
    while due.weekday() >= 5 or due in holidays:
        due += timedelta(days=1)
    return DeadlineResult(start_date=start, original_due_date=original_due, due_date=due)
