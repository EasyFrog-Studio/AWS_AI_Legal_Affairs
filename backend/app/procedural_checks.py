"""程序審查的自動判邏輯,獨立於擷取(F1)與檢索(F2/F3)——這裡判的是程序,不是內容。
含§77(1)必要記載事項與§77(3)當事人適格。
"""
from __future__ import annotations

import re
from typing import Optional

from pydantic import BaseModel

from app.models import CaseInfo, StandingAssessment, join_review_notes

# 訴願法§56 I 九款,只列可靠對應 CaseInfo 欄位、或能合理判斷的四款;
# 其餘五款(代理人-條件式、請求事項、受理機關、證據、年月日)CaseInfo 沒有對應欄位,
# 硬用正則猜只會製造假的「缺漏」,寧可誠實不檢核,不假裝檢核得到——
# 這是「說不準就不猜」原則在必要記載檢核上的體現。
# 每項可對應多個欄位:任一有值即視為該款齊備。§56 I⑤ 把「事實」與「理由」寫成同一款,
# 只認 appeal_reasons 的話,模型把訴願書內容全放進事實欄時會誤報「訴願書不合法定程式」。
_CHECKED_ITEMS: list[tuple[tuple[str, ...], str]] = [
    (("appellant",), "訴願人之姓名（第一款）"),
    (("agency",), "原行政處分機關（第三款）"),
    (("appeal_facts", "appeal_reasons"), "訴願之事實及理由（第五款）"),
    (("receipt_date",), "收受或知悉行政處分之年、月、日（第六款）"),
]
_UNCHECKED_NOTE = (
    "本版僅檢核訴願法第56條第1項第一、三、五、六款（對應CaseInfo已擷取欄位）；"
    "第二（代理人）、四（請求事項）、七（受理機關）、八（證據）、九（年月日）款未檢核，"
    "CaseInfo 無對應欄位、規則式判斷不可靠"
)


class RequiredFieldsCheck(BaseModel):
    """訴願法§56 I 必要記載事項的檢核結果。"""

    missing: list[str] = []  # 缺漏的款次中文名;空清單代表本版檢核範圍內的四款皆齊備
    # 缺漏是否屬「可補正」:訴願人姓名與原處分機關兩者皆缺時,案件本身無從特定,視為不能補正;
    # 其餘缺漏(事實理由、收受日)依訴願法§62 通知補正即可,絕大多數情形屬此類
    correctable: bool = True
    note: str = ""


class CorrectionNotice(BaseModel):
    """補正通知狀態。全部欄位皆由承辦人員輸入或卷內抽取,抽不到則維持預設(視為未通知)——
    這是刻意的保守預設:沒有補正通知的證據,就不能假設案件已經走完補正程序。
    """

    notified: bool = False  # 卷內是否查得補正通知
    corrected: bool = False  # 訴願人是否已於期限內補正
    overdue: bool = False  # 補正期限(訴願法§62:20日)是否已過而未補正


def check_required_fields(info: CaseInfo) -> RequiredFieldsCheck:
    """比對 CaseInfo 已擷取欄位是否齊備本版可檢核的四款。"""
    missing = [
        label
        for fields, label in _CHECKED_ITEMS
        if not any(_has_value(getattr(info, field)) for field in fields)
    ]
    if not missing:
        return RequiredFieldsCheck(missing=[], correctable=True, note=_UNCHECKED_NOTE)

    # 姓名與機關都缺,案件連基本身份都特定不了,通知補正也不知道通知誰——視為不能補正
    correctable = not (_is_missing_label(missing, "第一款") and _is_missing_label(missing, "第三款"))
    return RequiredFieldsCheck(missing=missing, correctable=correctable, note=_UNCHECKED_NOTE)


def _has_value(value) -> bool:
    """appeal_facts/appeal_reasons 是 list,其餘是 str;「未載明」是 F1 對抽不到欄位的制式填法,視為缺漏。"""
    if isinstance(value, list):
        return len(value) > 0
    return bool(value) and value != "未載明"


def _is_missing_label(missing: list[str], marker: str) -> bool:
    return any(marker in label for label in missing)


def apply_article_77_1(
    screening,
    required_check: RequiredFieldsCheck,
    notice: CorrectionNotice | None,
):
    """§77(1) 兩層判斷:缺欄位不等於直接不受理,
    必須「不能補正」或「經通知補正逾期不補正」才成立。

    | 情況 | 動作 |
    |---|---|
    | 本版檢核的四款皆齊備 | 不動 |
    | 缺漏但不能補正(姓名與機關皆缺) | 覆寫為第1款不受理,並標 review_note(自動判仍須人工複核) |
    | 缺漏可補正,卷內查得補正通知且逾期未補正 | 覆寫為第1款不受理,並標 review_note |
    | 缺漏可補正,卷內查得補正通知且已補正 | 不動(視為已補正齊備) |
    | 缺漏可補正,卷內無補正通知 | 不覆寫,標記「應通知補正」——不得逕採不受理結論 |

    無論哪一層動用了自動判,`review_note` 一律非空——自動判之後承辦人只做確認,
    這是自動判必須可被推翻的具體落實。
    注意:必要記載事項是客觀事實檢核,**不管模型原本判 `passed` 是 True 或 False**都適用——
    模型誤判受理但卷內缺法定要件時,一樣要覆寫成不受理;只有「本版檢核的四款皆齊備」才會
    直接放行不動,那時 `screening` 才不受影響。
    """
    if not required_check.missing:
        return screening

    missing_text = "、".join(required_check.missing)

    if not required_check.correctable:
        reasoning = f"訴願書缺漏{missing_text}，且訴願人與原處分機關均無法特定，不能補正。"
        return screening.model_copy(
            update={
                "passed": False,
                "matched_clause": "77條第1款",
                "reasoning": reasoning,
                "review_note": join_review_notes(screening.review_note, "自動判第1款不受理（不能補正），請人工確認"),
            }
        )

    if notice is None or not notice.notified:
        note = f"訴願書缺漏{missing_text}，應依訴願法第62條通知訴願人於20日內補正，尚無補正通知，不得逕為不受理。"
        return screening.model_copy(update={"review_note": join_review_notes(screening.review_note, note)})

    if notice.corrected:
        return screening  # 已補正齊備,不覆寫也不留標記

    if notice.overdue:
        reasoning = f"訴願書缺漏{missing_text}，經通知補正，逾期未補正。"
        return screening.model_copy(
            update={
                "passed": False,
                "matched_clause": "77條第1款",
                "reasoning": reasoning,
                "review_note": join_review_notes(screening.review_note, "自動判第1款不受理（逾期未補正），請人工確認"),
            }
        )

    # 已通知但補正期限未過、尚未補正:結果未定,不得預先判不受理
    note = f"訴願書缺漏{missing_text}，已通知補正，補正期限尚未屆至，結果未定。"
    return screening.model_copy(update={"review_note": join_review_notes(screening.review_note, note)})


class StandingCheck(BaseModel):
    """§77(3) 當事人適格檢核結果。語料 10 件,不論哪個分支都一律待人工確認
    (見 apply_article_77_3),這裡的欄位只記錄「查到了什麼」,不做結論性判斷。"""

    consistent: Optional[bool] = None  # 處分相對人與訴願人是否一致;任一欄空白時為 None,不猜
    has_standing: Optional[bool] = None  # 不一致時才有意義:LLM 依訴願法§18 判斷有無利害關係


def check_standing(info: CaseInfo) -> StandingCheck:
    """只比對兩個欄位是否一致,不含 LLM 判斷——has_standing 由呼叫端在不一致時另外取得
    (經 AIProvider.assess_standing),避免這支函式混入非同步的外部呼叫。"""
    if not _has_value(info.disposition_recipient) or not _has_value(info.appellant):
        return StandingCheck(consistent=None)
    return StandingCheck(consistent=info.disposition_recipient == info.appellant)


_ARTICLE_KEY_RE = re.compile(r".+#\d+(-\d+)?$")  # 「法規名稱#條號」,條號須為數字(可含 -1 等細分款)才算指得出來


def resolve_standing_assessment(assessment: StandingAssessment) -> Optional[bool]:
    """§77(3) 的核心攔阻:模型指不出具體保護規範(法規名稱＋條號)時,視同「無法判斷」,
    不論 has_standing 填了什麼都不採用——這是防「說得很順但沒有依據」的唯一有效攔法。"""
    if not _ARTICLE_KEY_RE.match(assessment.referenced_norm or ""):
        return None
    return assessment.has_standing


def apply_article_77_3(screening, check: StandingCheck):
    """§77(3) 當事人適格:處分相對人與訴願人一致就不用判利害關係,直接放行。
    不一致時,`check.has_standing`(已由呼叫端經 provider 判斷)決定是否覆寫:

    | 情況 | 動作 |
    |---|---|
    | consistent 為 None(任一欄空白) | 不覆寫,但標記未檢核——沒檢核過與檢核通過是兩件事 |
    | consistent=True(一致) | 不覆寫 |
    | consistent=False 且 has_standing=True(有利害關係) | 不覆寫,只標記爭點待確認 |
    | consistent=False 且 has_standing=False(無利害關係) | 覆寫為第3款不受理 |
    | consistent=False 且 has_standing=None(LLM 判斷不出來) | 不覆寫,標記待人工認定 |

    有無利害關係屬個案價值判斷,**任何一種不一致的情形都一律 `review_note` 非空**——
    即使覆寫成不受理,也不是可逕採的結論。
    """
    if check.consistent is None:
        note = "處分相對人或訴願人欄位缺漏，當事人適格未經檢核，須人工認定。"
        merged = ";".join(n for n in (screening.review_note, note) if n)
        return screening.model_copy(update={"review_note": merged})
    if check.consistent:
        return screening

    if check.has_standing is None:
        note = "處分相對人與訴願人不一致，利害關係判斷依據不足，須人工認定當事人適格。"
        return screening.model_copy(update={"review_note": join_review_notes(screening.review_note, note)})

    if check.has_standing:
        note = "處分相對人與訴願人不一致，但訴願人依訴願法第18條仍具法律上利害關係，須人工確認當事人適格爭點。"
        return screening.model_copy(update={"review_note": join_review_notes(screening.review_note, note)})

    reasoning = "處分相對人與訴願人不一致，且訴願人對原處分無法律上利害關係，依訴願法第18條不符訴願人適格要件。"
    return screening.model_copy(
        update={
            "passed": False,
            "matched_clause": "77條第3款",
            "reasoning": reasoning,
            "review_note": join_review_notes(screening.review_note, "自動判第3款不受理（利害關係屬價值判斷），請人工確認"),
        }
    )
