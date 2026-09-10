"""parse_answers 純函式測試(不讀 PDF、不連 DB)。
執行:cd preprocessing && python -m pytest test_parse_answers.py
"""
from parse_answers import build_rows, parse_header, split_sections

# 合成測資的寫法:標題字元間夾一到多個半形空白,證物欄帶全形冒號
TESTDATA_STYLE = """新北市政府環境保護局 訴願答辯書
訴 願 人：洪淑惠
原處分機關：新北市政府環境保護局
發文日期：中華民國114 年10 月15 日
發文字號：新北環稽字第1141948612 號

  訴願人因違反廢棄物清理法事件，不服本局裁處書，提起訴願，謹依法答辯如下：

答 辯 聲 明
  本件訴願不受理。

事    實
一、訴願人於112 年1 月3 日於水溝棄置雜物。

理    由
一、程序答辯：本件訴願人提起訴願已逾法定期間。

證物：
  1、系爭裁處書影本1 份。

此  致

  新北市政府（法制局訴願審議委員會）
"""

# 官方空白範本的寫法:標題只夾兩個空白,無發文欄位,機關留空白框
TEMPLATE_STYLE = """（原處分機關全銜）訴願答辯書
訴 願 人：○○○
原處分機關：○○○
    訴願人因○○事件，不服本機關處分，謹依法答辯如下：
答辯聲明
（載明對本件訴願之答辯聲明）
事  實
一、就事實經過依時間先後順序詳予敘述。
理  由
一、程序答辯：
此 致
"""


def test_sections_split_on_headings_whatever_the_padding():
    sections = split_sections(TESTDATA_STYLE)
    assert list(sections) == ["答辯聲明", "事實", "理由", "證物"]
    assert sections["答辯聲明"] == "本件訴願不受理。"
    assert "程序答辯" in sections["理由"]

    template = split_sections(TEMPLATE_STYLE)
    assert list(template) == ["答辯聲明", "事實", "理由"]


def test_the_addressee_block_after_此致_is_not_part_of_any_section():
    joined = "".join(split_sections(TESTDATA_STYLE).values())
    assert "法制局訴願審議委員會" not in joined


def test_a_document_without_section_headings_falls_back_to_whole_text():
    manual = "彰化縣政府訴願、行政訴訟答辯原則。壹、答辯要領。"
    assert split_sections(manual) == {"全文": manual}


def test_a_document_that_repeats_the_headings_is_not_sliced_into_sections():
    """要領手冊收錄多份範例答辯書,逐段切的話後面的「理由」會覆蓋前面的,整份退為全文。"""
    handbook = (TEMPLATE_STYLE + "\n") * 3

    sections = split_sections(handbook)

    assert list(sections) == ["全文"]
    assert sections["全文"].count("此 致") == 3  # 三份都在,沒有被第一個「此致」截掉


def test_a_pdf_with_no_text_layer_yields_no_sections():
    assert split_sections("   \n  ") == {}


def test_header_fields_are_read_from_the_answer_brief_head():
    header = parse_header(TESTDATA_STYLE)
    assert header["原處分機關"] == "新北市政府環境保護局"
    assert header["發文字號"] == "新北環稽字第1141948612 號"
    assert header["發文日期"].startswith("中華民國114")


def test_absent_header_fields_are_left_out_rather_than_filled_blank():
    # 欄位缺漏與「欄位存在但值是空字串」是兩件事,後者會讓下游以為抓到了機關名
    assert parse_header("訴願答辯書\n訴 願 人：○○○\n") == {}
    # 標籤在、值是空的(掃描件常見),同樣不算抓到
    assert parse_header("原處分機關：\n發文字號：   \n") == {}


def test_one_chunk_per_section_carrying_kind_and_section_in_the_text():
    rows = build_rows("測資", "答辯-example1", "TEST_DATA example1", TESTDATA_STYLE, "04_訴願答辯書.pdf")

    assert [r["id"] for r in rows] == [
        "答辯-example1#答辯聲明",
        "答辯-example1#事實",
        "答辯-example1#理由",
        "答辯-example1#證物",
    ]
    first = rows[0]
    assert first["text"].startswith("【訴願答辯書-測資】TEST_DATA example1 答辯聲明欄\n")
    assert first["metadata"] == {
        "doc_kind": "訴願答辯書",
        "source_kind": "測資",
        "section": "答辯聲明",
        "title": "TEST_DATA example1",
        "source_file": "04_訴願答辯書.pdf",
        "agency": "新北市政府環境保護局",
        "doc_date": "中華民國114 年10 月15 日",
        "doc_no": "新北環稽字第1141948612 號",
    }


def test_a_blank_template_still_produces_rows_but_without_agency_metadata():
    rows = build_rows("範本", "答辯-行政院範例", "行政院範例", TEMPLATE_STYLE, "行政院-訴願答辯書範例.pdf")

    assert len(rows) == 3
    assert rows[0]["metadata"]["source_kind"] == "範本"
    assert rows[0]["metadata"]["agency"] == "○○○"  # 範本的機關欄是空白框,照抄不改寫
    assert "doc_no" not in rows[0]["metadata"]


def test_an_empty_document_produces_no_rows():
    assert build_rows("測資", "答辯-x", "x", "", "x.pdf") == []


def test_a_long_manual_is_split_into_several_uniquely_keyed_chunks():
    manual = "壹、答辯要領。答辯書應載明本件訴願有無程序不合之情形。\n" * 120
    rows = build_rows("要領手冊", "答辯-要領", "彰化縣政府答辯要領", manual, "要領.pdf")

    assert len(rows) > 1
    assert {r["metadata"]["section"] for r in rows} == {"全文"}
    assert len({r["id"] for r in rows}) == len(rows)
