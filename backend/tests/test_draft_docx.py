"""決定書草稿的 Word 下載:逐區塊印 build_decision_blocks,與 PDF 同一份版面定義。"""
import io

import app.main as main_module
from app.config import settings
from app.docx_render import render_draft_docx
from app.models import Case, DecisionHeader, DraftResult
from app.pdf_render import decision_plain_text

TAB = "\t"
NEWLINE = "\n"


def _headers():
    return {"X-API-Key": settings.API_KEY}


def _case(case_id: str, **overrides) -> Case:
    fields = dict(
        case_id=case_id,
        created_at="2026-08-17T00:00:00+00:00",
        title="測試案件",
        source="text",
        input_text="訴願書內容",
        f4=DraftResult(
            draft_type="駁回", fact="事實欄內容。", reason="理由欄內容。", main_text="訴願駁回。"
        ),
        decision_header=DecisionHeader(case_no="1141021559", chairman="王主委"),
    )
    fields.update(overrides)
    main_module.store.create(Case(**fields))
    if fields.get("f4") is not None and "draft_plain_text" not in overrides:
        # 全文在 F4 產出時就攤平寫入(見 pipeline);測試直接塞 f4,得自己補這一步
        main_module.store.update(
            case_id, {"draft_plain_text": decision_plain_text(main_module.store.get(case_id))}
        )
    return main_module.store.get(case_id)


def _docx_text(blob: bytes) -> str:
    from docx import Document

    return "\n".join(p.text for p in Document(io.BytesIO(blob)).paragraphs)


def test_the_docx_carries_the_whole_document():
    text = _docx_text(render_draft_docx(_case("c-docx001")))

    assert "新北市政府訴願決定書" in text
    assert "案　　號：　1141021559" in text
    assert "主    文" in text and "訴願駁回。" in text
    assert "理    由" in text and "理由欄內容。" in text
    assert "訴願審議委員會主任委員  王主委" in text


def test_the_docx_prints_the_edited_body_inside_the_structured_header_and_footer():
    """承辦人改的是本文;結構化表頭與結尾(案號、主任委員)不因本文被整段換掉而消失。"""
    case = _case("c-docx002", draft_plain_text="承辦人自己排的版 第二行")
    text = _docx_text(render_draft_docx(case))

    assert "承辦人自己排的版 第二行" in text
    assert "案　　號：　1141021559" in text
    assert "訴願審議委員會主任委員  王主委" in text


def test_the_docx_keeps_the_inadmissible_form():
    case = _case(
        "c-docx003",
        f4=DraftResult(draft_type="不受理", fact="", reason="逾期提起。", main_text="訴願不受理。"),
    )
    text = _docx_text(render_draft_docx(case))

    assert "事    實" not in text  # 訴願法§89 I③ 得不記載
    assert "訴願不受理。" in text


def test_the_endpoint_returns_a_word_attachment():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-docx004")

    resp = client.get("/api/cases/c-docx004/draft.docx", headers=_headers())

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert "attachment" in resp.headers["content-disposition"]
    assert "訴願駁回。" in _docx_text(resp.content)


def test_a_case_without_a_draft_cannot_be_downloaded():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-docx005", f4=None)

    assert client.get("/api/cases/c-docx005/draft.docx", headers=_headers()).status_code == 409


def test_an_unknown_case_is_404():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    assert client.get("/api/cases/c-nodocx99/draft.docx", headers=_headers()).status_code == 404


def test_the_download_requires_the_api_key():
    from fastapi.testclient import TestClient

    client = TestClient(main_module.app)
    _case("c-docx006")

    assert client.get("/api/cases/c-docx006/draft.docx").status_code == 401


# ---------- 版面:抬頭靠右、條列懸掛縮排 ----------


def _paragraphs(blob: bytes):
    from docx import Document

    return list(Document(io.BytesIO(blob)).paragraphs)


def test_the_masthead_puts_the_case_number_on_a_right_aligned_tab_stop():
    """Word 的靠右不能用空白湊:字寬隨字型變,湊出來的位置在別人機器上就歪了。"""
    from docx.enum.text import WD_TAB_ALIGNMENT

    paragraphs = _paragraphs(render_draft_docx(_case("c-docx010")))
    masthead = next(p for p in paragraphs if p.text.startswith("新北市政府訴願決定書"))

    assert masthead.text == "新北市政府訴願決定書" + TAB + "案號：1141021559  號"
    stops = list(masthead.paragraph_format.tab_stops)
    assert len(stops) == 1
    assert stops[0].alignment == WD_TAB_ALIGNMENT.RIGHT


def test_numbered_items_carry_a_hanging_indent_so_word_wraps_under_the_text():
    case = _case("c-docx011", draft_plain_text="理　由" + NEWLINE + "一、按訴願法第 79 條規定…")
    paragraphs = _paragraphs(render_draft_docx(case))

    item = next(p for p in paragraphs if p.text.startswith("一、"))
    assert item.paragraph_format.left_indent is not None
    assert item.paragraph_format.first_line_indent < 0
    plain = next(p for p in paragraphs if p.text.startswith("訴願審議委員會主任委員"))
    assert plain.paragraph_format.left_indent is None


def test_no_paragraph_leaks_a_raw_tab_apart_from_the_masthead():
    paragraphs = _paragraphs(render_draft_docx(_case("c-docx012")))

    leaked = [p.text for p in paragraphs if TAB in p.text and not p.text.startswith("新北市政府")]
    assert leaked == []


def test_every_run_asks_word_for_the_ming_typeface_including_chinese():
    """只設 font.name 的話 Word 只換英數字,中文仍走版面預設字型;eastAsia 那一欄才管中文。"""
    from docx.oxml.ns import qn

    paragraphs = _paragraphs(render_draft_docx(_case("c-docx013")))
    runs = [run for p in paragraphs for run in p.runs]

    assert runs
    for run in runs:
        assert run.font.name == "新細明體"
        assert run._element.rPr.rFonts.get(qn("w:eastAsia")) == "新細明體"
