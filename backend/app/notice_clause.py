"""原處分書教示條款 -> 行政程序法§98 的期間認定分支。

§98 三項各是不同分支,不可混為一談:

| 卷內事實 | 期間 | 條項 |
|---|---|---|
| 未告知救濟期間,或告知錯誤且卷內明載未為更正 | 自處分書送達後一年 | III |
| 告知期間較法定期間長 | 依原告知之期間 | II |
| 告知錯誤且已通知更正 | 自更正通知送達之翌日起算法定期間 | I |

本模組只做「卷內事實 -> 走哪一支」,實際算日期在 deadline.py。三支都是語料零件
(90 件不受理決定書無一件據§98 認定期間),故非 statutory 的分支一律回非空 review_note;
呼叫端 reconcile_deadline 見 review_note 非空即不覆寫程序審查,不必另立旗標。

⚠️ §98 I 與 III 的分野繫於「有無通知更正」這個事實,而更正通知是第四份文書,
本系統的文件槽(訴願書/送達證書/原處分書/訴願答辯書)本來就收不到它。因此:
- 卷內**明載**未通知更正 -> 走 III(一年);
- 卷內**明載**更正通知及其送達日 -> 走 I;
- 卷內**沒提** -> 回 undetermined 明說抽不到,**不得以「無更正通知」當預設值頂替**。
  頂替的方向雖對人民有利(期間變一年),但那是系統替承辦人捏造了一個卷內沒有的事實。
"""
import re
from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

from app.deadline import APPEAL_PERIOD_DAYS, format_roc, fullwidth, roc_to_date
from app.deadline_extract import ROC_DATE_PATTERN

# 教示句判斷:以「句子裡同時談到救濟途徑、又寫了 N 日內」為準。不去比對「不服本處分」之類的
# 固定套語——各機關寫法不一,漏一種就會把有教示的案子誤判成未教示(方向不利人民)。
_SENTENCE_SPLIT = re.compile(r"[。\n；;]")
_REMEDY_HINT = re.compile(r"訴願|救濟|聲明不服|行政訴訟")
_DAYS_IN = re.compile(r"(\d+|[一二三四五六七八九十]+)\s*日(?:之)?內")

_CN_DIGITS = {c: i for i, c in enumerate("一二三四五六七八九", start=1)}

# 「未為更正」的明載;必須先於 _CORRECTION_NOTICE 判斷,否則「未通知更正」會被當成有更正通知
_CORRECTION_ABSENT = re.compile(r"未(?:曾|經|予|以通知)?(?:通知)?更正|未為更正|漏未更正")
_CORRECTION_NOTICE = re.compile(r"通知更正|更正通知|更正救濟期間|更正之通知")
# 更正通知的送達日:兩種語序都收(日期在前、日期在後),跨句不比對免得吃到原處分的送達日
_CORRECTION_SERVICE_PATTERNS = [
    rf"更正[^。\n]{{0,20}}?通知[^。\n]{{0,20}}?{ROC_DATE_PATTERN}(?=[^。\n]{{0,12}}?(?:送達|收受))",
    rf"{ROC_DATE_PATTERN}(?=[^。\n]{{0,12}}?(?:送達|收受)[^。\n]{{0,16}}?更正)",
    rf"更正[^。\n]{{0,12}}?通知(?:書)?(?:於|之)?\s*{ROC_DATE_PATTERN}",
]

Basis = Literal[
    "statutory",  # 教示完整且期間等於法定期間:§98 不介入,照訴願法§14 算
    "one_year",  # §98 III
    "stated_longer",  # §98 II
    "restart_from_correction",  # §98 I
    "undetermined",  # 教示有問題但分支定不出來:不得給逾期結論
    "not_assessed",  # 卷內無原處分書可看:沒檢查過就不要裝作檢查過
]


class NoticePeriodRule(BaseModel):
    """教示條款的認定結果。review_note 非空即代表結論不可逕採(statutory 以外恆非空)。"""

    basis: Basis = "statutory"
    stated_days: Optional[int] = None  # 教示所載期間(basis=stated_longer 時為期間長度)
    correction_service_date: Optional[date] = None  # 更正通知送達日(basis=restart_from_correction)
    finding: str = ""  # 卷內事實的認定,寫進 DeadlineCheck.detail 供決定書理由引用
    review_note: str = ""


def _cn_to_int(text: str) -> Optional[int]:
    """「三十」/「30」/「二十」-> int;看不懂就回 None,不猜。"""
    if text.isdigit():
        return int(text)
    if "十" in text:
        head, _, tail = text.partition("十")
        tens = _CN_DIGITS.get(head, 0) if head else 1  # 「十日」= 10,「三十日」= 30
        ones = _CN_DIGITS.get(tail, 0) if tail else 0
        return tens * 10 + ones if tens else None
    return _CN_DIGITS.get(text)


def _stated_days(text: str) -> Optional[int]:
    """教示所載的救濟期間日數;找不到即視為未告知救濟期間。"""
    for sentence in _SENTENCE_SPLIT.split(text):
        if not _REMEDY_HINT.search(sentence):
            continue
        match = _DAYS_IN.search(sentence)
        if match is None:
            continue
        days = _cn_to_int(match.group(1))
        if days is not None:
            return days
    return None


def _correction_service_date(text: str) -> Optional[date]:
    for pattern in _CORRECTION_SERVICE_PATTERNS:
        match = re.search(pattern, text)
        if match is None:
            continue
        try:
            return roc_to_date(match.group("y"), match.group("m"), match.group("d"))
        except ValueError:  # 卷內偶有錯字,轉不成日期就當這一種寫法沒抽到
            continue
    return None


def classify_notice_clause(
    clause_text: str, disposition_text: str, case_text: str = ""
) -> NoticePeriodRule:
    """(f1.disposition_notice_clause, 原處分書全文, 全卷文字) -> §98 分支。

    clause_text 先查、disposition_text 後查:模型抽的教示條款可能被截斷,截斷後找不到日數
    就退回原處分書全文再找一次,免得把「抽取不全」誤判成「機關未告知」。兩處都找不到日數,
    在原處分書確實有文字的前提下才認定未告知——這一點只有在原處分書在手時才敢下結論。
    """
    if not disposition_text.strip() and not clause_text.strip():
        # 訴願書/送達證書/原處分書三槽皆為必填(見 main.create_case),production 走不到這裡;單一字串測試入口與
        # 舊測資會。不阻斷計算,但要說出「沒查」,不可靜默當作教示完整。
        return NoticePeriodRule(
            basis="not_assessed",
            review_note="卷內無原處分書，有無教示救濟期間未經檢核",
        )

    days = _stated_days(clause_text) or _stated_days(disposition_text)
    if days is None:
        return NoticePeriodRule(
            basis="one_year",
            finding="原處分書未告知救濟期間",
            review_note=(
                "原處分書未告知救濟期間，期間依行政程序法第９８條第３項認定為一年"
                "（此分支未經語料驗證），不據以覆寫程序審查，須人工確認"
            ),
        )

    if days == APPEAL_PERIOD_DAYS:
        return NoticePeriodRule(
            basis="statutory", stated_days=days, finding=f"原處分書教示救濟期間{fullwidth(days)}日，與法定期間相符"
        )

    if days > APPEAL_PERIOD_DAYS:
        return NoticePeriodRule(
            basis="stated_longer",
            stated_days=days,
            finding=f"原處分書告知之救濟期間{fullwidth(days)}日較法定{fullwidth(APPEAL_PERIOD_DAYS)}日為長",
            review_note=(
                f"告知期間{fullwidth(days)}日較法定期間為長，依行政程序法第９８條第２項以原告知期間計算"
                "（此分支未經語料驗證），不據以覆寫程序審查，須人工確認"
            ),
        )

    # days < 法定期間:告知錯誤。§98 I 與 III 的分野在「有無通知更正」,卷內通常沒有這份文書。
    text = case_text or disposition_text
    if _CORRECTION_ABSENT.search(text):
        return NoticePeriodRule(
            basis="one_year",
            stated_days=days,
            finding=f"原處分書告知之救濟期間{fullwidth(days)}日有錯誤，卷內載明未為更正",
            review_note=(
                f"告知期間{fullwidth(days)}日有錯誤且卷內載明未為更正，期間依行政程序法第９８條第３項"
                "認定為一年（此分支未經語料驗證），不據以覆寫程序審查，須人工確認"
            ),
        )

    if _CORRECTION_NOTICE.search(text):
        corrected_on = _correction_service_date(text)
        if corrected_on is None:
            return NoticePeriodRule(
                basis="undetermined",
                stated_days=days,
                finding=f"原處分書告知之救濟期間{fullwidth(days)}日有錯誤，卷內載有更正通知",
                review_note=(
                    "更正通知之送達日無法認定，行政程序法第９８條第１項之法定期間起算日"
                    "因而無從認定，須人工調閱更正通知"
                ),
            )
        return NoticePeriodRule(
            basis="restart_from_correction",
            stated_days=days,
            correction_service_date=corrected_on,
            finding=(
                f"原處分書告知之救濟期間{fullwidth(days)}日有錯誤，更正通知於{format_roc(corrected_on)}送達"
            ),
            review_note=(
                "期間依行政程序法第９８條第１項自更正通知送達之翌日起算"
                "（此分支未經語料驗證），不據以覆寫程序審查，須人工確認"
            ),
        )

    return NoticePeriodRule(
        basis="undetermined",
        stated_days=days,
        finding=f"原處分書告知之救濟期間{fullwidth(days)}日與法定{fullwidth(APPEAL_PERIOD_DAYS)}日不符",
        review_note=(
            f"告知期間{fullwidth(days)}日有錯誤，而卷內未見有無通知更正之記載——更正通知非本系統收受之"
            "三份文書，不得逕認未為更正。期間應依行政程序法第９８條第１項或第３項認定，須人工調閱卷證"
        ),
    )
