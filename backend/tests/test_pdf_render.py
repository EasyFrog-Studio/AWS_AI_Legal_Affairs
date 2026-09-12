"""決定書草稿的版面:體例照 data_show/decisions_114/ 的 21 份真實決定書。
下載下來要是一份可直接送出的決定書,系統填得出來的填,填不出來的留空給承辦人。"""
from app.models import Case, CaseInfo, DecisionHeader, DraftResult
from app.pdf_render import build_decision_blocks, decision_body_text

TAB = "\t"
FULL_SPACE = "　"
NEWLINE = "\n"


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


def _f4(draft_type="駁回"):
    return DraftResult(draft_type=draft_type, fact="事實", reason="理由", main_text="主文")


def test_the_masthead_carries_the_authority_name_and_the_default_case_number():
    """案號預設帶案件編號:機關收文編號要承辦人填,但欄位不能一片空白,連抬頭右側都沒有。"""
    blocks = build_decision_blocks(_case(f1=_info(), f4=DraftResult(
        draft_type="駁回", fact="事實內容", reason="理由內容", main_text="訴願駁回。")))
    texts = _texts(blocks)

    assert f"案　　號：{FULL_SPACE}c-pdf0001" in texts
    kind, masthead = next(b for b in blocks if b[1].startswith("新北市政府訴願決定書"))
    assert kind == "split"
    assert masthead.endswith("案號：c-pdf0001  號")


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


# ---------- 本文三段(decision_body_text):draft_plain_text 的內容 ----------


def test_the_three_sections_appear_in_the_official_order_with_their_content():
    case = _case(f1=_info(), f4=DraftResult(
        draft_type="駁回", fact="緣訴願人於114年…", reason="一、按廢棄物清理法…", main_text="訴願駁回。"))
    body = decision_body_text(case)

    assert body.index("主　文") < body.index("事　實") < body.index("理　由")
    assert "訴願駁回。" in body
    assert "緣訴願人於114年…" in body
    assert "一、按廢棄物清理法…" in body


def test_an_inadmissible_decision_omits_the_fact_section_entirely():
    """語料 90 件不受理決定書事實欄全部為空(訴願法§89 I(3)),不留沒有內文的標題。"""
    case = _case(f1=_info(), f4=DraftResult(
        draft_type="不受理", fact="", reason="一、按訴願法第77條…", main_text="訴願不受理。"))
    body = decision_body_text(case)

    assert "事　實" not in body
    assert "主　文" in body and "理　由" in body


def test_a_revoking_decision_keeps_the_fact_section():
    case = _case(f1=_info(), f4=DraftResult(
        draft_type="原處分撤銷", fact="緣訴願人…", reason="一、按建築法…", main_text="原處分撤銷。"))
    body = decision_body_text(case)

    assert "主　文" in body and "事　實" in body and "理　由" in body


def test_a_partially_inadmissible_partially_dismissed_decision_carries_the_notice_and_keeps_fact_section():
    """部分不受理部分駁回對訴願人不利(仍有駁回部分),附教示段且不省略事實欄。"""
    case = _case(f1=_info(), f4=DraftResult(
        draft_type="部分不受理部分駁回", fact="緣訴願人…", reason="一、關於罰鍰部分…",
        main_text="關於罰鍰部分,訴願駁回。關於限期改善部分,訴願不受理。"))
    body = decision_body_text(case)
    joined = "".join(_texts(build_decision_blocks(case)))

    assert "事　實" in body
    assert "如不服本決定，得於決定書送達之次日起 2 個月內向臺北高等行政法院" in joined


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
        assert "如不服本決定，得於決定書送達之次日起 2 個月內向臺北高等行政法院" in joined, draft_type
        assert "臺北市士林區福國路 101 號" in joined, draft_type


def test_a_revoking_decision_carries_no_litigation_notice():
    """語料 3 件 81I 撤銷案全部沒有這段——訴願有理由,訴願人沒有要救濟的對象。"""
    joined = "".join(_texts(build_decision_blocks(_case(f1=_info(), f4=_f4("原處分撤銷")))))

    assert "如不服本決定" not in joined


def test_a_revoke_and_remand_decision_carries_no_litigation_notice():
    """撤銷另處與原處分撤銷同為訴願有理由,語料 11 件撤銷另處案同樣沒有這段。"""
    joined = "".join(_texts(build_decision_blocks(_case(f1=_info(), f4=_f4("撤銷另處")))))

    assert "如不服本決定" not in joined


def test_an_unfilled_decision_date_prints_the_era_word_alone():
    """畫面上那一列只有欄名,列印就只印欄名;補一排年月日空格線是畫面上沒有的內容。"""
    blocks = build_decision_blocks(_case(f1=_info(), f4=_f4()))
    date_line = next(t for _k, t in blocks if t.startswith("中華民國"))

    assert date_line == "中華民國　"


def test_the_configured_ming_font_is_used_when_the_file_exists(tmp_path, monkeypatch):
    """決定書要用新細明體;字型以檔案內嵌,PDF 的文字才抽得回來(fitz 內建 china-t 沒有 ToUnicode)。"""
    from app import pdf_render

    font_file = tmp_path / "uming.ttf"
    font_file.write_bytes(b"not-a-real-font")  # 只驗選檔邏輯,不驗字型解析
    monkeypatch.setattr(pdf_render.settings, "DECISION_FONT_FILE", str(font_file))

    fontname, fontfile = pdf_render.resolve_font()

    assert fontfile == str(font_file)
    assert fontname != "china-t"


def test_the_officer_supplied_case_number_replaces_the_blank():
    """案號、日期、委員名單是機關收文後才定的,系統填不出來;承辦人填了就要印上去,
    不能只在畫面上看得到而下載的 PDF 還是空白——版面只有 build_decision_blocks 這一份定義。"""
    case = _case(
        f1=_info(),
        f4=_f4(),
        decision_header=DecisionHeader(case_no="1140700123", decided_date="114年10月15日"),
    )
    texts = _texts(build_decision_blocks(case))

    assert any("1140700123" in t for t in texts)
    assert any(t.startswith("中華民國") and "114年10月15日" in t for t in texts)


def test_the_officer_supplied_committee_replaces_the_blank_lines():
    """語料每案 10~14 位委員,系統只留空行;填了名單就照名單印,不再多留空行。"""
    case = _case(
        f1=_info(),
        f4=_f4(),
        decision_header=DecisionHeader(chairman="王主委", committee="李委員\n張委員"),
    )
    texts = _texts(build_decision_blocks(case))

    assert any(t.startswith("訴願審議委員會主任委員") and "王主委" in t for t in texts)
    member_lines = [t for t in texts if t.startswith("委員")]
    assert len(member_lines) == 2
    assert "李委員" in member_lines[0] and "張委員" in member_lines[1]


def test_the_parties_can_be_corrected_without_touching_f1():
    """訴願人姓名在卷內與擷取結果不一致時,承辦人直接改決定書上的字,
    不必為了印對一個名字而去改 F1(那會連帶影響程序審查與檢索)。"""
    case = _case(f1=_info(), f4=_f4(), decision_header=DecisionHeader(appellant="鄭○芳"))
    texts = _texts(build_decision_blocks(case))

    assert any(t.strip().startswith("訴願人") and "鄭○芳" in t for t in texts)
    assert not any(t.strip().startswith("訴願人") and "鄭婉芳" in t for t in texts)


def test_an_empty_header_field_prints_the_label_alone():
    """列印的內容要與畫面上的決定書草稿一致:空欄就是空的,不補空白格也不猜委員人數。"""
    case = _case(f1=_info(), f4=_f4(), decision_header=DecisionHeader(gist="只填要旨"))
    texts = _texts(build_decision_blocks(case))

    assert f"案　　號：{FULL_SPACE}" in texts
    assert f"要　　旨：{FULL_SPACE}只填要旨" in texts
    assert [t for t in texts if t.startswith("委員")] == ["委員  "]
    assert "訴願審議委員會主任委員  " in texts


# ---------- 結構化表頭五欄:案號/要旨/發文日期/發文字號/相關法條 ----------


def test_the_gist_and_dates_are_printed_from_the_structured_header():
    header = DecisionHeader(
        gist="因違反建築法事件提起訴願",
        issued_date="民國114年12月17日",
        issued_no="新北府訴決字第1141934721號",
    )
    joined = "".join(_texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header))))

    assert "要　　旨：　因違反建築法事件提起訴願" in joined
    assert "發文日期：　民國114年12月17日" in joined  # 表頭欄位自帶紀年,樣本即如此
    assert "發文字號：　新北府訴決字第1141934721號" in joined


def test_a_blank_issued_no_prints_nothing_after_its_label():
    """發文字號發文時才由案管系統配;畫面上是空的,列印就不該冒出一組套語空號。"""
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=DecisionHeader(gist="只填要旨"))))

    assert f"發文字號：{FULL_SPACE}" in texts
    assert not any("新北府訴決字第" in t for t in texts)


def test_related_laws_print_one_law_per_line():
    header = DecisionHeader(related_laws="訴願法 第 81 條\n建築法 第 2 條")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert "相關法條：　訴願法 第 81 條" in texts
    assert any(line.strip() == "建築法 第 2 條" for line in texts)


def test_empty_related_laws_prints_a_blank_line():
    joined = "".join(_texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=DecisionHeader()))))

    assert "相關法條：　" in joined


# ---------- 代理人列:agent_name 空整列不印,標籤依 agent_role ----------


def test_the_agent_row_is_always_there_because_the_officer_sees_it():
    """當事人三列是畫面上那份欄位表,列印照印;要不要留這一列由承辦人決定。"""
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=DecisionHeader(agent_name=""))))

    assert "    代理人  " in texts


def test_an_agent_row_uses_the_officer_supplied_role_label():
    header = DecisionHeader(agent_role="送達代收人", agent_name="陳大文")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert any(t.strip().startswith("送達代收人") and "陳大文" in t for t in texts)
    assert not any(t.strip().startswith("代理人") for t in texts)


def test_an_agent_row_defaults_to_the_agent_label_when_the_role_is_blank():
    header = DecisionHeader(agent_role="", agent_name="陳大文")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert any(t.strip().startswith("代理人") and "陳大文" in t for t in texts)


# ---------- 體例:逐字對照語料 17.114年-違反廢棄物清理法事件-79I-訴願無理由-駁回 ----------


def test_the_meta_block_spaces_two_character_labels_to_four_and_ends_with_the_full_text_label():
    """查詢系統印出來的表頭標籤一律四字寬:「案　　號」與「發文日期」左右對齊。"""
    header = DecisionHeader(case_no="1141061379", gist="因違反廢棄物清理法事件提起訴願")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert "案　　號：　1141061379" in texts
    assert "要　　旨：　因違反廢棄物清理法事件提起訴願" in texts
    assert "全　　文：" in texts
    assert texts.index("全　　文：") > texts.index("案　　號：　1141061379")


def test_related_law_continuation_lines_align_under_the_first_law():
    header = DecisionHeader(related_laws="訴願法 第 79 條\n行政罰法 第 18 條")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert "相關法條：　訴願法 第 79 條" in texts
    assert "　　　　　　行政罰法 第 18 條" in texts


def test_the_masthead_is_one_split_line_with_the_case_number_on_the_right():
    """語料的抬頭是同一列:機關名靠左、案號靠右。畫面分成兩行,列印時併回一列。"""
    header = DecisionHeader(case_no="1141061379")
    blocks = build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header))

    kind, text = next(b for b in blocks if b[1].startswith("新北市政府訴願決定書"))
    assert kind == "split"
    left, right = text.split(TAB)
    assert left == "新北市政府訴願決定書"
    assert right == "案號：1141061379  號"


def test_a_blank_case_number_leaves_the_masthead_without_a_right_hand_column():
    blocks = build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=DecisionHeader(gist="只填要旨")))

    _kind, text = next(b for b in blocks if b[1].startswith("新北市政府訴願決定書"))
    assert TAB not in text


def test_the_parties_are_indented_four_spaces_with_a_two_space_gap():
    header = DecisionHeader(appellant="鄭○芳", agent_role="代理人", agent_name="陳大文",
                            agency="新北市政府環境保護局")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert "    訴願人  鄭○芳" in texts
    assert "    代理人  陳大文" in texts
    assert "    原處分機關  新北市政府環境保護局" in texts


def test_the_body_headings_are_indented_and_spaced_out_for_printing():
    """承辦人編輯的是「主　文」,印出來要是語料的「    主    文」;本文內容逐字不動。"""
    case = _case(f1=_info(), f4=_f4(), draft_plain_text="主　文\n訴願駁回。\n\n理　由\n一、按訴願法…")
    texts = _texts(build_decision_blocks(case))
    body = next(t for t in texts if "訴願駁回。" in t)

    assert "    主    文" in body
    assert "    理    由" in body
    assert "主　文" not in body
    assert "一、按訴願法…" in body


def test_the_committee_block_uses_two_space_gaps():
    header = DecisionHeader(chairman="蔡庭榕", committee="陳明燦\n陳立夫")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert "訴願審議委員會主任委員  蔡庭榕" in texts
    assert "委員  陳明燦" in texts and "委員  陳立夫" in texts


def test_the_decision_date_does_not_repeat_the_era_word():
    """日期欄存的是標準寫法「民國114年12月11日」,抬頭模板自己寫了中華民國。"""
    header = DecisionHeader(decided_date="民國114年12月11日")
    texts = _texts(build_decision_blocks(_case(f1=_info(), f4=_f4(), decision_header=header)))

    assert "中華民國　114年12月11日" in texts
    assert not any("中華民國民國" in t for t in texts)


# ---------- 列印:抬頭靠右、條列續行懸掛縮排 ----------


def _pdf_lines(blob):
    """回傳 [(x0, 這一行的文字)],依版面由上而下。"""
    import fitz

    doc = fitz.open(stream=blob, filetype="pdf")
    out = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                text = "".join(span["text"] for span in line["spans"])
                out.append((round(line["bbox"][0], 1), round(line["bbox"][2], 1), text))
    return out


def test_the_case_number_is_printed_flush_right_on_the_masthead_line():
    """語料的抬頭是機關名靠左、案號靠右的同一列;製表符是版面指令,不能印成字。"""
    from app.pdf_render import render_draft_pdf

    case = _case(f1=_info(), f4=_f4(), decision_header=DecisionHeader(case_no="1141061379"))
    lines = _pdf_lines(render_draft_pdf(case))

    assert not any(TAB in text for _x0, _x1, text in lines)
    title = next(l for l in lines if l[2].startswith("新北市政府訴願決定書"))
    number = next(l for l in lines if "1141061379" in l[2] and "案號" in l[2])
    assert abs(title[0] - number[0]) > 100  # 案號不接在機關名後面,是另一端
    assert number[1] > title[1] + 100  # 靠右界收尾


def test_a_wrapped_numbered_item_hangs_under_its_own_text():
    """理由欄逐條編號,折行對齊到條號之後;不縮排會讓下一行看起來像新的一條。"""
    from app.pdf_render import render_draft_pdf

    long_item = "一、按廢棄物清理法第 4 條規定：" + "○" * 120
    case = _case(f1=_info(), f4=_f4(), draft_plain_text=f"理　由{NEWLINE}{long_item}")
    lines = [l for l in _pdf_lines(render_draft_pdf(case)) if "○" in l[2]]

    assert len(lines) > 1, "測資不夠長,沒有折行就量不到懸掛縮排"
    assert not lines[0][2].startswith(" ")
    assert lines[1][2].startswith("    ")  # 續行退到條號之後
