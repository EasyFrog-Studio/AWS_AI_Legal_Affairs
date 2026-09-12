"""決定書本文與全文的分工。

draft_plain_text 只含本文(主文/事實/理由),承辦人編輯的就是它;表頭與結尾是 DecisionHeader
的結構化欄位,兩者攤平合成 decision_full_text 才是 PDF/Word 實際印出的那一份決定書。
"""
import app.main as main_module
from app.models import Case, DecisionHeader, DraftResult
from app.pdf_render import decision_full_text, decision_plain_text


def _headers():
    from app.config import settings

    return {"X-API-Key": settings.API_KEY}


def _case(case_id: str, **overrides) -> Case:
    fields = dict(
        case_id=case_id,
        created_at="2026-08-17T00:00:00+00:00",
        title="測試案件",
        source="text",
        input_text="訴願書內容",
        f4=DraftResult(
            draft_type="駁回",
            fact="事實欄內容。",
            reason="理由欄內容。",
            main_text="訴願駁回。",
        ),
        decision_header=DecisionHeader(case_no="1141021559"),
    )
    fields.update(overrides)
    case = Case(**fields)
    main_module.store.create(case)
    return main_module.store.get(case_id)


# ---------- draft_plain_text 只含本文 ----------


def test_plain_text_is_just_the_three_sections():
    """表頭與結尾不重印在 draft_plain_text 裡——那是承辦人實際編輯的範圍,只有本文。"""
    case = _case("c-plain001")
    text = decision_plain_text(case)

    assert "新北市政府訴願決定書" not in text
    assert "訴願審議委員會主任委員" not in text
    assert "主　文" in text and "訴願駁回。" in text
    assert "理　由" in text and "理由欄內容。" in text


def test_plain_text_keeps_the_inadmissible_form():
    """不受理案的事實欄依訴願法§89 I③ 不記載,攤平時也不該無中生有一個空標題。"""
    case = _case(
        "c-plain002",
        f4=DraftResult(draft_type="不受理", fact="", reason="逾期提起。", main_text="訴願不受理。"),
    )
    text = decision_plain_text(case)

    assert "事　實" not in text
    assert "主　文" in text


# ---------- decision_full_text:結構化表頭 + 本文 + 結構化結尾,欄位序對齊正式決定書 ----------


def _header(**overrides):
    base = dict(
        case_no="1143051259",
        gist="因違反建築法事件提起訴願",
        issued_date="民國114年12月17日",
        issued_no="新北府訴決字第1141934721號",
        related_laws="訴願法 第 81 條",
        appellant="劉○鑫",
        agency="新北市政府工務局",
        preamble="上列訴願人因違反建築法事件…本府依法決定如下：",
        chairman="蔡庭榕",
        committee="陳明燦",
        decided_date="114年12月17日",
    )
    base.update(overrides)
    return DecisionHeader(**base)


def test_decision_full_text_follows_the_official_gazette_field_order_for_an_admissible_case():
    case = _case(
        "c-full001",
        f4=DraftResult(draft_type="原處分撤銷", fact="緣訴願人…", reason="一、按建築法…", main_text="原處分撤銷。"),
        decision_header=_header(),
    )
    text = decision_full_text(case)

    order = [
        "案　　號：　1143051259",
        "要　　旨：　因違反建築法事件提起訴願",
        "發文日期：　民國114年12月17日",
        "發文字號：　新北府訴決字第1141934721號",
        "相關法條：　訴願法 第 81 條",
        "新北市政府訴願決定書",
        "劉○鑫",
        "新北市政府工務局",
        "本府依法決定如下：",
        "主    文",
        "原處分撤銷。",
        "事    實",
        "緣訴願人…",
        "理    由",
        "一、按建築法…",
        "訴願審議委員會主任委員  蔡庭榕",
        "委員  陳明燦",
    ]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert text.startswith("案　　號：　")
    assert "如不服本決定" not in text  # 原處分撤銷訴願有理由,無救濟對象


def test_decision_full_text_follows_the_official_gazette_field_order_for_an_inadmissible_case():
    case = _case(
        "c-full002",
        f4=DraftResult(draft_type="不受理", fact="", reason="逾期提起。", main_text="訴願不受理。"),
        decision_header=_header(issued_no="", related_laws=""),
    )
    text = decision_full_text(case)

    order = [
        "案　　號：　1143051259",
        "要　　旨：　因違反建築法事件提起訴願",
        "發文日期：　民國114年12月17日",
        "發文字號：　",
        "相關法條：　",
        "新北市政府訴願決定書",
        "劉○鑫",
        "新北市政府工務局",
        "本府依法決定如下：",
        "主    文",
        "訴願不受理。",
        "理    由",
        "逾期提起。",
        "訴願審議委員會主任委員  蔡庭榕",
        "如不服本決定，得於決定書送達之次日起 2 個月內向臺北高等行政法院",
    ]
    positions = [text.index(marker) for marker in order]
    assert positions == sorted(positions)
    assert "事　實" not in text


# ---------- 全文的編輯與下載 ----------


def test_editing_the_text_is_stored_verbatim():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-plain004")
    edited = "新北市政府訴願決定書 承辦人自己排的版 主文:訴願駁回。"

    resp = client.patch(
        "/api/cases/c-plain004/draft-text", json={"text": edited}, headers=_headers()
    )

    assert resp.status_code == 200
    assert main_module.store.get("c-plain004").draft_plain_text == edited


def test_editing_a_case_without_a_draft_is_refused():
    """還沒有 f4 就沒有決定書可改;放行等於讓人對著一個不存在的東西打字。"""
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-plain006", f4=None)

    resp = client.patch("/api/cases/c-plain006/draft-text", json={"text": "x"}, headers=_headers())

    assert resp.status_code == 409
    assert main_module.store.get("c-plain006").draft_plain_text == ""


def test_an_unknown_case_is_404():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    resp = client.patch("/api/cases/c-nosuch99/draft-text", json={"text": "x"}, headers=_headers())
    assert resp.status_code == 404


# ---------- PDF 印的是攤平後的整份決定書 ----------


def test_the_pdf_renders_the_stored_body_text():
    import fitz

    from app.pdf_render import render_draft_pdf

    case = _case("c-plain009").model_copy(
        update={"draft_plain_text": "承辦人自己排的版 第二行"}
    )
    pdf = fitz.open(stream=render_draft_pdf(case), filetype="pdf")
    text = "".join(page.get_text() for page in pdf)

    assert "承辦人自己排的版" in text
    assert "第二行" in text
    assert "新北市政府訴願決定書" in text  # 表頭與結尾不隨本文被整段換掉而消失


def test_an_empty_text_still_renders_a_pdf():
    """全文被清空時不該拋例外——承辦人可能正打算整份重寫,下載仍要拿得到一份空白稿紙。"""
    import fitz

    from app.pdf_render import render_draft_pdf

    case = _case("c-plain010").model_copy(update={"draft_plain_text": ""})
    pdf = fitz.open(stream=render_draft_pdf(case), filetype="pdf")
    assert pdf.page_count >= 1
