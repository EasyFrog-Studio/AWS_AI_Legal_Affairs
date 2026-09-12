"""app.draft_text 的邊界:舊全文(含表頭與結尾)讀出時剝成本文,新格式原樣通過。"""
from app.draft_text import body_from_legacy_text, is_legacy_full_text


def _legacy_text(fact_section: str) -> str:
    return (
        "新北市政府訴願決定書\n"
        "案　　號：1140700123\n"
        "　訴願人　王大明\n"
        "　原處分機關　新北市政府工務局\n"
        "上列訴願人因違反建築法事件，不服原處分機關民國114年1月1日新北工使字第1號"
        "所為之處分，提起訴願一案，本府依法決定如下：\n"
        "主　文\n訴願駁回。\n\n"
        f"{fact_section}"
        "理　由\n一、按…\n\n"
        "訴願審議委員會主任委員　蔡庭榕\n"
        "委員　陳明燦\n"
        "中華民國114年1月1日\n"
    )


def test_an_admissible_legacy_document_is_stripped_down_to_its_three_sections():
    """受理案的舊全文含事實欄,剝完仍要留著——訴願法沒有免記載的理由。"""
    text = _legacy_text("事　實\n緣訴願人…\n\n")
    body = body_from_legacy_text(text)

    assert body.startswith("主　文")
    assert "事　實" in body and "緣訴願人…" in body
    assert "理　由" in body
    assert "新北市政府訴願決定書" not in body
    assert "訴願審議委員會主任委員" not in body


def test_an_inadmissible_legacy_document_has_no_fact_section_to_strip():
    """不受理案的舊全文本來就沒有事實欄(訴願法§89 I③),剝出來的本文也不該無中生有一段。"""
    text = _legacy_text("")
    body = body_from_legacy_text(text)

    assert body.startswith("主　文")
    assert "事　實" not in body
    assert "理　由" in body


def test_a_body_only_text_is_returned_verbatim():
    """已經是本文格式(沒有標題與結尾)就原樣回傳,不重複處理。"""
    body_text = "主　文\n訴願駁回。\n\n理　由\n一、按…"

    assert body_from_legacy_text(body_text) == body_text


def test_text_without_any_section_heading_is_returned_unchanged_not_dropped():
    """承辦人整份自己重打、沒有留下任何段標題時原樣回傳——找不到段落不代表可以丟內容。"""
    freeform = "承辦人自己排的版 第二行"

    assert body_from_legacy_text(freeform) == freeform


def test_an_empty_string_is_returned_unchanged():
    assert body_from_legacy_text("") == ""


def test_is_legacy_full_text_detects_the_title_or_the_tail_marker():
    assert is_legacy_full_text("新北市政府訴願決定書\n案　　號：1140700123") is True
    assert is_legacy_full_text("訴願審議委員會主任委員　王主委\n委員　陳明燦") is True


def test_is_legacy_full_text_is_false_for_body_only_text():
    assert is_legacy_full_text("主　文\n訴願駁回。\n\n理　由\n一、按…") is False
