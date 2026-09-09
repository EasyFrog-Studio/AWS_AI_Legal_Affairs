"""needs_review 的單一判準:哪幾件不能直接送。

五種來源取聯集(見實作計畫 Ticket 11)。集中在這裡是因為清單頁與詳情頁必須用同一套判斷——
兩邊各寫一份的下場是清單說可以送、頁首說要複核。
"""
from app.models import Case
from app.procedural_checks import check_required_fields, check_standing


def needs_review(case: Case) -> bool:
    """任一來源成立即為真。尚未分析的案件不算待複核(那只是還沒跑,不是有疑義),
    但三槽未全確認是例外——那是收案階段就該處理的事實。"""
    if any(doc.check.matched is not True or doc.review_note for doc in case.documents.values()):
        return True
    if case.deadline is not None and case.deadline.review_note:
        return True
    if case.screening is not None and case.screening.review_note:
        return True
    if case.f1 is not None:
        if check_required_fields(case.f1).missing:
            return True
        if check_standing(case.f1).consistent is False:
            return True
    return False
