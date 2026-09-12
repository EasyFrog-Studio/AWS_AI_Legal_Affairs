"""app.decision_header 的邊界:F4 落地那一刻寫進 case.decision_header 的初始值。"""
from app.decision_header import decision_header_defaults, related_laws_from_cited
from app.models import Case, CaseInfo, DraftResult


def test_related_laws_from_cited_formats_the_article_number():
    lines = related_laws_from_cited(["訴願法#81", "建築法#77"])

    assert lines == "訴願法 第 81 條\n建築法 第 77 條"


def test_related_laws_from_cited_keeps_items_without_an_article_number():
    """行政函釋等參考見解沒有條號可格式化,原樣保留而不是被規則吃掉。"""
    lines = related_laws_from_cited(["台內營字第1234號函釋", "訴願法#81"])

    assert lines == "台內營字第1234號函釋\n訴願法 第 81 條"


def _f1(**overrides):
    base = dict(
        appellant="王大明",
        agency="新北市政府工務局",
        disposition_date="114年1月1日",
        disposition_no="新北工使字第1號",
        disposition_summary="裁處罰鍰",
        case_type="建築法",
        agent_role="代理人",
        agent_name="李代理",
    )
    base.update(overrides)
    return CaseInfo(**base)


def _f4(**overrides):
    base = dict(
        draft_type="駁回", fact="事實", reason="理由", main_text="主文",
        cited_laws=["建築法#77"], gist="因違反建築法事件提起訴願",
    )
    base.update(overrides)
    return DraftResult(**base)


def _case(**overrides):
    base = dict(
        case_id="c-hdr0001", created_at="2026-09-10T00:00:00+00:00",
        title="表頭測試案", source="text", input_text="卷證",
    )
    base.update(overrides)
    return Case(**base)


def test_decision_header_defaults_fill_from_f1_and_f4():
    case = _case(f1=_f1(), f4=_f4())
    header = decision_header_defaults(case)

    assert header.case_no == case.case_id
    assert header.gist == "因違反建築法事件提起訴願"
    assert header.related_laws == "建築法 第 77 條"
    assert header.appellant == "王大明"
    assert header.agent_role == "代理人"
    assert header.agent_name == "李代理"
    assert header.agency == "新北市政府工務局"


def test_decision_header_defaults_tolerate_a_missing_f1():
    """F1 沒跑或抽不到時表頭不能整段炸掉,當事人欄留空給承辦人補,案號仍由 case_id 帶出。"""
    case = _case(case_id="c-hdr0002", f1=None, f4=_f4())
    header = decision_header_defaults(case)

    assert header.case_no == "c-hdr0002"
    assert header.appellant == ""
    assert header.agency == ""
    assert header.agent_name == ""
    assert header.gist == "因違反建築法事件提起訴願"  # 仍取自 f4,不受 f1 缺席影響


def test_decision_header_defaults_leave_the_signature_and_gazette_fields_blank():
    """主任委員、委員、發文日期/字號、決定日期由承辦人自己填,系統不猜。"""
    case = _case(f1=_f1(), f4=_f4())
    header = decision_header_defaults(case)

    assert header.chairman == ""
    assert header.committee == ""
    assert header.issued_date == ""
    assert header.issued_no == ""
    assert header.decided_date == ""
