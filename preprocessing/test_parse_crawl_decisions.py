from pathlib import Path

import pytest

from parse_crawl_decisions import (
    SectionSplitError,
    bucket_for_case_type,
    classify_role,
    extract_clause,
    extract_related_laws,
    extract_result,
    get_case_body,
    parse_filename,
    parse_pdf_text,
    split_items,
    split_main_sections,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- 檔名解析 ---

def test_ntpc_filename_has_no_letter_prefix_and_group_defaults_to_ntpc():
    info, err = parse_filename("110-08-27_1101060734_空氣污染防制法.pdf")
    assert err is None
    assert info["group"] == "NTPC"
    assert info["case_no"] == "1101060734"
    assert info["source_file"] == "NTPC-1101060734"
    assert info["case_type"] == "空氣污染防制法"
    assert info["year"] == "110"
    assert info["is_official"] is False


def test_ty_filename_keeps_city_letters_in_case_no_but_not_in_source_file():
    info, err = parse_filename("113-09-26_TY1130183868_洗錢防制法.pdf")
    assert err is None
    assert info["group"] == "TY"
    assert info["case_no"] == "TY1130183868"
    assert info["source_file"] == "TY-1130183868"


def test_official_suffix_is_stripped_from_case_type_and_flagged():
    info, err = parse_filename("110-02-04_1090071465_陳情_官方.pdf")
    assert err is None
    assert info["case_type"] == "陳情"
    assert info["is_official"] is True


def test_a_filename_that_does_not_match_the_date_caseno_type_shape_fails_loudly():
    info, err = parse_filename("不是這個格式.pdf")
    assert info is None
    assert err is not None


# --- 案型 -> 桶 ---

def test_exact_match_case_types_route_to_their_own_bucket():
    assert bucket_for_case_type("空氣污染防制法") == "空氣污染防制法"
    assert bucket_for_case_type("洗錢防制法") == "洗錢防制法"


def test_free_text_variants_and_combined_case_types_fall_back_to_other():
    # 與既有語料一致:只有完全相等才落自己的桶,「空氣污染防治法」(治不是制)這種變體不算
    assert bucket_for_case_type("空氣污染防治法") == "其他案型"
    assert bucket_for_case_type("地價稅罰鍰") == "其他案型"
    assert bucket_for_case_type("申請提供政府資訊") == "其他案型"


# --- 版型統一:抽出「內文」 ---

def test_labeled_header_format_extracts_the_body_after_the_full_text_label():
    body = get_case_body(_read("official_label_header.txt"))
    assert body.startswith("新北市政府訴願決定書")
    assert "案　　號：" not in body
    assert "要　　旨：" not in body


def test_direct_format_has_no_header_labels_and_is_used_as_is():
    raw = _read("ntpc_general.txt")
    body = get_case_body(raw)
    assert body == raw.strip()


def test_nav_shell_junk_before_the_case_number_label_is_dropped():
    body = get_case_body(_read("reexam_navshell.txt"))
    assert "查閱內容" not in body
    assert "網站導覽" not in body
    assert body.startswith("新北市政府訴願再審決定書")


def test_tc_labeled_header_uses_the_body_label_instead_of_full_text():
    body = get_case_body(_read("tc_moneylaunder_inline.txt"))
    assert body.startswith("臺中市政府訴願決定書")
    assert "決定書日期：" not in body


# --- 主文/事實/理由 切分 ---

def test_ntpc_general_splits_into_facts_and_reasons():
    body = get_case_body(_read("ntpc_general.txt"))
    sections = split_main_sections(body)
    assert sections["主文"] == "訴願不受理。"
    assert sections["事實"] == ""
    assert sections["理由"].startswith("一、按訴願係人民對行政機關之行政處分")
    assert "訴願審議委員會主任委員" not in sections["理由"]


def test_multi_caseno_doc_splits_facts_and_reasons_across_pages():
    body = get_case_body(_read("ntpc_multi_caseno.txt"))
    sections = split_main_sections(body)
    assert "原處分撤銷" in sections["主文"]
    assert sections["事實"].startswith("緣訴願人愛○○園公寓大廈管理委員會")
    assert sections["理由"].startswith("一、關於訴願人愛○○園公寓大廈管理委員會部分")


def test_inline_no_facts_header_still_splits_via_the_decision_anchor():
    # 桃園/臺中版主文與理由常與內文同一行接排,且沒有事實段
    body = get_case_body(_read("tc_moneylaunder_inline.txt"))
    sections = split_main_sections(body)
    assert sections["主文"] == "訴願不受理。"
    assert sections["事實"] == ""
    assert sections["理由"].startswith("一、 本件相關法令如下")
    assert "訴願審議委員會兼主任委員" not in sections["理由"]


def test_facts_only_doc_with_no_reason_header_is_relabelled_by_its_conclusion():
    # 事實段落含「據上論結」等結論語時,判定理由誤標成事實,整段併入理由
    body = get_case_body(_read("ty_facts_only.txt"))
    sections = split_main_sections(body)
    assert sections["事實"] == ""
    assert sections["理由"].startswith("一、 法令依據")
    assert "據上論結" in sections["理由"]


def test_missing_decision_anchor_raises_instead_of_returning_empty_sections():
    with pytest.raises(SectionSplitError):
        split_main_sections("這是一份完全不含「決定如下」與任何標題的文字。")


def test_missing_reason_and_no_conclusion_language_raises():
    with pytest.raises(SectionSplitError):
        split_main_sections("本府依法決定如下：\n主 文\n訴願駁回。\n事 實\n只有事實，沒有任何結論語。\n訴願審議委員會主任委員 王大明")


# --- paragraph_role 啟發式 ---

def test_facts_section_items_are_always_facts_narrative():
    assert classify_role("事實", "一、訴願意旨略謂：訴願人主張如下", is_last=False) == "事實敘述"
    assert classify_role("事實", "緣訴願人於本市設置停車場使用", is_last=False) == "事實敘述"


def test_reason_items_starting_with_a_citation_verb_are_law_citation():
    text = "一、按都市計畫法第 4 條規定：「本法之主管機關…」"
    assert classify_role("理由", text, is_last=False) == "法規引述"


def test_the_last_reason_item_with_concluding_language_is_the_closing_paragraph():
    text = "六、綜上論結，本件訴願為無理由，依訴願法第 79 條第 1 項規定，決定如主文。"
    assert classify_role("理由", text, is_last=True) == "結語"


def test_a_rebuttal_of_a_specific_petitioner_argument_is_other_item():
    text = "五、至訴願人主張已於114年6月30日停止營業等語。惟按相關規定，難認可採。"
    assert classify_role("理由", text, is_last=False) == "其他項次"


def test_case_specific_application_of_law_is_substantive_reasoning():
    text = "四、卷查訴願人於訴外人所有系爭土地未經核准供作收費停車場使用，違反都市計畫法第79條第1項規定。"
    assert classify_role("理由", text, is_last=False) == "本案論理"


def test_a_bare_continuation_opener_with_no_numbering_is_continuation():
    assert classify_role("理由", "又，訴願人所舉事證均與本案無涉。", is_last=False) == "接續段"


def test_an_unremarkable_numbered_item_falls_back_to_other_item():
    text = "三、本府 110 年 3 月 25 日新北府城都字第 11005211581 號公告發布，自同年 3 月 29 日起實施相關要點。"
    assert classify_role("理由", text, is_last=False) == "其他項次"


# --- 條文擷取 ---

def test_related_laws_are_deduplicated_in_first_seen_order():
    text = "違反建築法第 77 條第 1 項規定，依同法第 91 條第 1 項第 2 款規定，另按建築法第 77 條規定辦理。"
    assert extract_related_laws(text) == "建築法 第77條；建築法 第91條"


def test_related_laws_keep_a_zhi_suffix_when_present():
    text = "違反洗錢防制法第15條之2第1項規定，爰依同條第2項規定告誡。"
    assert extract_related_laws(text) == "洗錢防制法 第15條之2"


def test_clause_reads_the_appeal_article_77_subsection():
    text = "本件訴願為程序不合，依訴願法第 77 條第 6 款規定，決定如主文。"
    assert extract_clause(text) == "§77(6)"


def test_clause_falls_back_to_79_or_81_when_no_subsection_cited():
    assert extract_clause("依訴願法第 79 條第 1 項規定，決定如主文。") == "§79"
    assert extract_clause("爰將原處分撤銷，由原處分機關另為適法之處分，依訴願法第 81 條第 1 項規定。") == "§81"


def test_result_is_read_from_the_operative_text():
    # 值域比照既有語料 {駁回,不受理,撤銷,部分,其他},沒有獨立的「原處分撤銷」值
    assert extract_result("訴願不受理。") == "不受理"
    assert extract_result("訴願駁回。") == "駁回"
    assert extract_result("原處分撤銷，由原處分機關另為適法之處分。") == "撤銷"


# --- 一案多號:只用檔名案號 ---

def test_multi_caseno_document_uses_only_the_filename_case_number(tmp_path):
    info, err = parse_filename("110-03-08_1093091487_建築法.pdf")
    assert err is None
    raw = _read("ntpc_multi_caseno.txt")
    rows, error = parse_pdf_text(info, raw)
    assert error is None
    assert rows, "應至少產生一個 chunk"
    assert all(r["metadata"]["source_file"] == "NTPC-1093091487" for r in rows)
    assert all(r["metadata"]["case_no"] == "1093091487" for r in rows)
    # 內文另一個案號 1093091451 不應該被拿來當 case_no
    assert not any("1093091451" == r["metadata"]["case_no"] for r in rows)


# --- 片段名格式 + 完整列 ---

def test_chunk_id_matches_source_file_section_three_digit_sequence():
    info, _ = parse_filename("110-08-27_1101060734_空氣污染防制法.pdf")
    raw = _read("ntpc_general.txt")
    rows, error = parse_pdf_text(info, raw)
    assert error is None
    ids = [r["片段名"] for r in rows]
    assert ids == sorted(ids)
    for chunk_id in ids:
        assert chunk_id.startswith("NTPC-1101060734-理由-")
        seq = chunk_id.rsplit("-", 1)[1]
        assert len(seq) == 3 and seq.isdigit()


def test_full_row_contract_has_all_fourteen_metadata_keys():
    info, _ = parse_filename("110-08-27_1101060734_空氣污染防制法.pdf")
    raw = _read("ntpc_general.txt")
    rows, error = parse_pdf_text(info, raw)
    assert error is None
    expected_keys = {
        "source_file", "year", "case_type", "clause", "result", "case_no", "doc_no",
        "related_laws", "doc_type", "source", "source_url", "有實體論理", "section",
        "paragraph_role",
    }
    for row in rows:
        assert set(row["metadata"].keys()) == expected_keys
        assert row["來源檔"] == row["metadata"]["source_file"]
        assert row["文件類型"] == "訴願決定書"
        assert row["段落角色"] == row["metadata"]["paragraph_role"]


def test_no_reason_title_produces_a_failure_not_a_silent_empty_output():
    info, _ = parse_filename("110-01-01_9999999999_測試.pdf")
    rows, error = parse_pdf_text(info, "本府依法決定如下：\n主 文\n訴願駁回。\n沒有任何標題與結論語的內容。")
    assert rows == []
    assert error is not None
