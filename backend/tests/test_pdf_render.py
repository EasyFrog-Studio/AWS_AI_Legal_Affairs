"""決定書草稿的版面:體例照 data/TEST_DATA/_參考-114年決定書全文 的 21 份真實決定書。
下載下來要是一份可直接送出的決定書,系統填得出來的填,填不出來的留空給承辦人。"""
from app.models import Case, CaseInfo, DraftResult
from app.pdf_render import build_decision_blocks


def _case(f1=None, f4=None, **overrides):
    base = dict(
        case_id="c-pdf0001",
        created_at="2026-09-10T00:00:00+00:00",
        title="版面測試案",
        status="done",
        current_stage="done",
        source="pdf",
        input_text="卷證內容",
        f1=f1,
        f4=f4,
    )
    base.update(overrides)
    return Case(**base)


def _info(**overrides):
    base = dict(
        appellant="鄭婉芳",
        agency="新北市政府環境保護局",
        disposition_date="114年9月16日",
        disposition_no="新北環稽字第41-114-090351號",
        disposition_summary="隨地拋棄煙蒂裁處罰鍰",
        case_type="廢棄物清理法",
    )
    base.update(overrides)
    return CaseInfo(**base)


def _texts(blocks):
    return [text for _kind, text in blocks]


def test_the_masthead_carries_the_authority_name_and_an_empty_case_number():
    """案號是機關收文後才編的,系統編不出來,留空白讓承辦人填,而不是拿 case_id 冒充。"""
    blocks = build_decision_blocks(_case(f1=_info(), f4=DraftResult(
        draft_type="駁回", fact="事實內容", reason="理由內容", main_text="訴願駁回。")))
    texts = _texts(blocks)

    assert ("title", "新北市政府訴願決定書") in blocks
    case_no_line = next(t for t in texts if t.startswith("案"))
    assert "號" in case_no_line
    assert "c-pdf0001" not in "".join(texts)


def test_the_parties_and_the_disposition_under_appeal_come_from_f1():
    blocks = build_decision_blocks(_case(f1=_info(), f4=DraftResult(
        draft_type="駁回", fact="事實內容", reason="理由內容", main_text="訴願駁回。")))
    joined = "".join(_texts(blocks))

    assert "訴願人" in joined and "鄭婉芳" in joined
    assert "原處分機關" in joined and "新北市政府環境保護局" in joined
    assert "因廢棄物清理法事件" in joined
    assert "114年9月16日" in joined and "新北環稽字第41-114-090351號" in joined
    assert "本府依法決定如下" in joined


def test_a_case_without_f1_still_renders_the_skeleton_with_blanks():
    """F1 沒跑或抽不到時不能整段消失——那會讓承辦人以為這份決定書沒有當事人欄。"""
    blocks = build_decision_blocks(_case(f1=None, f4=DraftResult(
        draft_type="不受理", fact="", reason="理由內容", main_text="訴願不受理。")))
    joined = "".join(_texts(blocks))

    assert "訴願人" in joined
    assert "原處分機關" in joined
    assert "None" not in joined


def test_the_three_sections_appear_in_the_official_order_with_their_content():
    blocks = build_decision_blocks(_case(f1=_info(), f4=DraftResult(
        draft_type="駁回", fact="緣訴願人於114年…", reason="一、按廢棄物清理法…", main_text="訴願駁回。")))
    headings = [t for k, t in blocks if k == "heading"]
    joined = "".join(_texts(blocks))

    assert headings == ["主　文", "事　實", "理　由"]
    assert "訴願駁回。" in joined
    assert "緣訴願人於114年…" in joined
    assert "一、按廢棄物清理法…" in joined


def test_an_inadmissible_decision_omits_the_fact_section_entirely():
    """語料 90 件不受理決定書事實欄全部為空(訴願法§89 I(3)),不留沒有內文的標題。"""
    blocks = build_decision_blocks(_case(f1=_info(), f4=DraftResult(
        draft_type="不受理", fact="", reason="一、按訴願法第77條…", main_text="訴願不受理。")))
    headings = [t for k, t in blocks if k == "heading"]

    assert headings == ["主　文", "理　由"]


def test_a_revoking_decision_keeps_the_fact_section():
    blocks = build_decision_blocks(_case(f1=_info(), f4=DraftResult(
        draft_type="原處分撤銷", fact="緣訴願人…", reason="一、按建築法…", main_text="原處分撤銷。")))
    headings = [t for k, t in blocks if k == "heading"]

    assert headings == ["主　文", "事　實", "理　由"]


def _f4(draft_type="駁回"):
    return DraftResult(draft_type=draft_type, fact="事實", reason="理由", main_text="主文")


def test_the_committee_signature_block_is_labelled_but_left_blank():
    """委員人數逐案不同(語料 10~14 人),系統不猜名單也不猜人數,只留標籤與空白。"""
    blocks = build_decision_blocks(_case(f1=_info(), f4=_f4()))
    texts = _texts(blocks)

    chair = next(t for t in texts if t.startswith("訴願審議委員會主任委員"))
    assert chair.replace("訴願審議委員會主任委員", "").strip("　 ") == ""
    assert sum(1 for t in texts if t.startswith("委員")) >= 1
    assert not any("蔡庭榕" in t or "陳明燦" in t for t in texts)


def test_dismissed_and_inadmissible_decisions_carry_the_administrative_litigation_notice():
    """語料 18 件不受理/駁回案全部附此教示,逐字相同。"""
    for draft_type in ("駁回", "不受理"):
        joined = "".join(_texts(build_decision_blocks(_case(f1=_info(), f4=_f4(draft_type)))))
        assert "如不服本決定,得於決定書送達之次日起 2 個月內向臺北高等行政法院" in joined, draft_type
        assert "臺北市士林區福國路 101 號" in joined, draft_type


def test_a_revoking_decision_carries_no_litigation_notice():
    """語料 3 件 81I 撤銷案全部沒有這段——訴願有理由,訴願人沒有要救濟的對象。"""
    joined = "".join(_texts(build_decision_blocks(_case(f1=_info(), f4=_f4("原處分撤銷")))))

    assert "如不服本決定" not in joined


def test_the_issue_date_line_is_left_blank_for_the_officer():
    blocks = build_decision_blocks(_case(f1=_info(), f4=_f4()))
    date_line = next(t for _k, t in blocks if t.startswith("中華民國"))

    assert "年" in date_line and "月" in date_line and "日" in date_line
    assert not any(ch.isdigit() for ch in date_line)


def test_the_configured_kai_font_is_used_when_the_file_exists(tmp_path, monkeypatch):
    """決定書要用標楷體;字型以檔案內嵌,PDF 的文字才抽得回來(fitz 內建 china-t 沒有 ToUnicode)。"""
    from app import pdf_render

    font_file = tmp_path / "ukai.ttc"
    font_file.write_bytes(b"not-a-real-font")  # 只驗選檔邏輯,不驗字型解析
    monkeypatch.setattr(pdf_render.settings, "DECISION_FONT_FILE", str(font_file))

    fontname, fontfile = pdf_render.resolve_font()

    assert fontfile == str(font_file)
    assert fontname != "china-t"


def test_a_missing_font_file_falls_back_to_the_builtin_cjk_font(tmp_path, monkeypatch):
    """字型沒裝進 image 時仍要印得出決定書——退回內建字型,代價是 PDF 文字複製出來是亂碼。"""
    from app import pdf_render

    monkeypatch.setattr(pdf_render.settings, "DECISION_FONT_FILE", str(tmp_path / "不存在.ttc"))

    fontname, fontfile = pdf_render.resolve_font()

    assert (fontname, fontfile) == ("china-t", None)


def test_slot_mode_replaces_the_editable_sections_with_markers():
    """網站上的決定書要與 PDF 同一套版面,差別只在三段本文是可編輯欄位。
    版面只有一份定義,前端不再自己拼一次骨架,才不會兩邊長不一樣。"""
    case = _case(f1=_info(), f4=DraftResult(
        draft_type="駁回", fact="事實內容", reason="理由內容", main_text="訴願駁回。"))

    blocks = build_decision_blocks(case, body_as_slots=True)

    assert ("slot", "main_text") in blocks
    assert ("slot", "fact") in blocks
    assert ("slot", "reason") in blocks
    joined = "".join(t for _k, t in blocks)
    assert "訴願駁回。" not in joined and "事實內容" not in joined
    # 骨架其餘部分與 PDF 完全一致
    assert [k for k, _t in blocks] == [
        k if k != "body" or t not in ("訴願駁回。", "事實內容", "理由內容") else "slot"
        for k, t in build_decision_blocks(case)
    ]


def test_slot_mode_still_omits_the_fact_slot_for_an_inadmissible_decision():
    case = _case(f1=_info(), f4=DraftResult(
        draft_type="不受理", fact="", reason="理由內容", main_text="訴願不受理。"))

    slots = [t for k, t in build_decision_blocks(case, body_as_slots=True) if k == "slot"]

    assert slots == ["main_text", "reason"]
