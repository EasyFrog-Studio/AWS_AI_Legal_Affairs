"""決定書全文:承辦人編輯與下載的唯一對象。

f4 三欄是模型產出的素材,產出時攤平成這份全文(pipeline);之後改的、印的、下載的都是它。
"""
import app.main as main_module
from app.models import Case, DecisionHeader, DraftResult
from app.pdf_render import decision_plain_text


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


# ---------- 純文字是從真正的版面攤平出來的,不是空白框 ----------


def test_plain_text_carries_the_real_document():
    """切過去要看到的是這份決定書本身,不是一張白紙——否則承辦人得整份重打。"""
    case = _case("c-plain001")
    text = decision_plain_text(case)

    assert "新北市政府訴願決定書" in text
    assert "案　　號:1141021559" in text
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


# ---------- 全文即決定書 ----------


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


# ---------- PDF 印的就是那一份全文 ----------


def test_the_pdf_renders_the_stored_text():
    import fitz

    from app.pdf_render import render_draft_pdf

    case = _case("c-plain009").model_copy(
        update={"draft_plain_text": "承辦人自己排的版 第二行"}
    )
    pdf = fitz.open(stream=render_draft_pdf(case), filetype="pdf")
    text = "".join(page.get_text() for page in pdf)

    assert "承辦人自己排的版" in text
    assert "第二行" in text


def test_an_empty_text_still_renders_a_pdf():
    """全文被清空時不該拋例外——承辦人可能正打算整份重寫,下載仍要拿得到一份空白稿紙。"""
    import fitz

    from app.pdf_render import render_draft_pdf

    case = _case("c-plain010").model_copy(update={"draft_plain_text": ""})
    pdf = fitz.open(stream=render_draft_pdf(case), filetype="pdf")
    assert pdf.page_count >= 1
