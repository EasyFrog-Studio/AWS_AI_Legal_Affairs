"""needs_review 的單一判準:哪幾件不能直接送。

九種來源取聯集。集中在這裡是因為清單頁與詳情頁必須用同一套判斷——
兩邊各寫一份的下場是清單說可以送、頁首說要複核。
"""
import re

from app.models import Case
from app.procedural_checks import check_required_fields, check_standing

_REMAND_PERIOD_RE = re.compile(r"\d+\s*(?:日|個月)內")  # 訴願法§81 II:撤銷發回應指定相當期間,主文須寫出具體日數或月數


def needs_review(case: Case) -> bool:
    """任一來源成立即為真。尚未分析的案件不算待複核(那只是還沒跑,不是有疑義),
    但文件槽未全確認是例外——那是收案階段就該處理的事實。"""
    # 空槽的「未確認」不算:選填槽沒有文件就沒有東西可確認,而答辯書是機關受理後才送來的,
    # 收案當下本來就沒有——把它算進來的話每一件新案都恆亮,待複核這個訊號就廢了。
    # 判準與 /analyze 的擋門(main.py)一致:那裡早就放行空槽。
    if any(
        (doc.check.matched is not True and doc.text.strip()) or doc.review_note
        for doc in case.documents.values()
    ):
        return True
    if case.deadline is not None and case.deadline.review_note:
        return True
    if case.screening is not None and case.screening.review_note:
        return True
    # 不受理那一側由 enforce_inadmissible_format 保證體例,受理這一側無人把關,
    # 模型仍可能吐出「不受理」草稿;不臆測正確的決定類型,只把矛盾標出來。
    if case.track == "admissible" and case.f4 is not None and case.f4.draft_type == "不受理":
        return True
    # 模型交出空欄位時系統原本一聲不吭,畫面與 PDF 就是一份欄位空白的決定書。
    # 例外只有不受理案的事實欄——訴願法§89 I③ 得不記載,語料 90 件全空,標了就是誤報。
    if case.f4 is not None:
        if not case.f4.reason.strip() or not case.f4.main_text.strip():
            return True
        if case.f4.draft_type != "不受理" and not case.f4.fact.strip():
            return True
        # 語料僅 1 件,主文逐標的分項是否對得上原處分的各個標的,系統驗不了
        if case.f4.draft_type == "部分不受理部分駁回":
            return True
        if case.f4.draft_type == "撤銷另處" and not _REMAND_PERIOD_RE.search(case.f4.main_text):
            return True
    if case.f1 is not None:
        if check_required_fields(case.f1).missing:
            return True
        if check_standing(case.f1).consistent is False:
            return True
    return False
